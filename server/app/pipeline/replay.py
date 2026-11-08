"""Answers from stored files instead of live agents, with scripted failures for tests. Demo mode uses this.

Results pass through the same contract models as HTTP, and runs live in memory, so a restart forgets them just as an agent can.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..clock import Clock
from ..context import Tenant
from ..stores import STORES
from ..tables import ROW
from .client import AgentReply, reply_from
from .output_control import AGENTS

REPLAY_DIR = Path(__file__).resolve().parents[3] / "demo" / "replay"
FAILS = Literal["404", "late", "duplicate", "truncated", "cut_off", "invalid", "failed", "slow", "tool_error"]


@dataclass
class ReplayScript:
    """404: forgets the run `times` times. late: finishes after `after_seconds`. duplicate: POST already has the result.
    truncated: a `length` failure. cut_off: JSON that stops midway. invalid: breaks the schema. failed: a failed run.
    slow: never finishes. tool_error: finishes, reporting a tool failure."""

    kind: FAILS
    after_seconds: float = 0
    times: int = 1


@dataclass
class _Run:
    agent: str
    key: tuple[str, str]
    body: Mapping[str, Any]
    result: Optional[dict]
    script: Optional[ReplayScript]
    ready_at: Any
    cancelled: bool = False


def _key(agent: str, request: Mapping[str, Any]) -> tuple[str, str]:
    if "case" not in request:  # a run for the whole workspace, such as the Gap Analyst's
        return request["tenant"], agent
    if agent == "challenger":
        return request["case"]["case_id"], "challenge-" + request["target"]["finding_id"]
    what = {"dossier-reader": "dossier-reader", "coordinator": "coordinator", "sameness": "sameness"}.get(agent)
    if what is None:
        what = request.get("check", {}).get("id", agent)
    return request["case"]["case_id"], what


def _fill(value: Any, request: Mapping[str, Any]) -> Any:
    """A stored answer can echo a field of its request, as a brief echoes its fingerprint or a check id."""
    if isinstance(value, str) and value.startswith("$request."):
        found: Any = request
        for step in value.removeprefix("$request.").split("."):  # `results.0.check_id` walks into lists too
            found = found[int(step)] if isinstance(found, list) else found[step]
        return found
    if isinstance(value, dict):
        return {k: _fill(v, request) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, request) for v in value]
    return value


class ReplayAgentClient:
    def __init__(self, clock: Clock, root: Path = REPLAY_DIR):
        self.clock = clock
        self.root = root
        self.runs: dict[str, _Run] = {}
        self.answers: dict[tuple[str, str], dict] = {}
        self.scripts: dict[tuple[str, str], list[ReplayScript]] = {}
        self.calls: list[tuple[str, str]] = []  # (start, poll or cancel; run id)
        self.before_call = None  # a test's hook, to stop a worker at the moment of the call

    @classmethod
    def from_files(cls, clock: Clock, root: Path = REPLAY_DIR) -> "ReplayAgentClient":
        return cls(clock, root)

    def forget_runs(self) -> None:
        """Drops the runs already started. After a demo reset the cases start again with the same run ids, and a known id with a different request is refused."""
        self.runs.clear()
        self.calls.clear()

    def answer(self, case_id: str, what: str, result: dict) -> None:
        """Replaces the stored answer for an agent, or for one check of the assessor."""
        self.answers[(case_id, what)] = result

    def script(self, case_id: str, what: str, *scripts: ReplayScript) -> None:
        self.scripts.setdefault((case_id, what), []).extend(scripts)

    def calls_to(self, op: str, run_id: str) -> int:
        return self.calls.count((op, run_id))

    def _stored(self, key: tuple[str, str]) -> Optional[dict]:
        if key in self.answers:
            return self.answers[key]
        path = self.root / "runs" / key[0] / f"{key[1]}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def _envelope(self, agent: str, run_id: str, status: str, **fields) -> dict:
        info = {"name": agent, "version": "replay-1", "prompt_hash": "sha256:" + hashlib.sha256(agent.encode()).hexdigest()[:16]}
        return {"run_id": run_id, "status": status, "agent": info, **fields}

    def _finished(self, run_id: str, run: _Run) -> Any:
        kind = run.script.kind if run.script else None
        now = self.clock.now().isoformat()
        if run.result is None:
            return self._envelope(run.agent, run_id, "failed", finish_reason="error", finished_at=now,
                                  error={"code": "no_recording", "message": "No stored answer for this run.", "retryable": False})
        if kind == "truncated":
            return self._envelope(run.agent, run_id, "failed", finish_reason="length", finished_at=now,
                                  error={"code": "cut_off", "message": "The answer was cut off.", "retryable": True})
        if kind == "failed":
            return self._envelope(run.agent, run_id, "failed", finish_reason="error", finished_at=now,
                                  error={"code": "model_error", "message": "The model failed.", "retryable": True})
        body = self._envelope(run.agent, run_id, "completed", finish_reason="stop", finished_at=now, result=_fill(run.result, run.body),
                              usage={"model_calls": 1, "tool_calls": 1, "input_tokens": 100, "output_tokens": 10})
        if kind == "tool_error":
            body["tool_errors"] = [{"tool": "read_page", "code": "timeout", "message": "The tool timed out.", "retryable": True}]
        if kind == "invalid":
            body = {**body, "result": {"outcome": {"kind": "finding", "status": "pass", "summary": "Approved.", "reasoning": "Approved."}}}
        if kind == "cut_off":
            return json.dumps(body)[:60]
        return body

    def _answer(self, status: int, run_id: str, run: _Run) -> AgentReply:
        if run.cancelled:
            body = self._envelope(run.agent, run_id, "cancelled", finish_reason="cancelled")
        elif run.script and run.script.kind == "slow" or self.clock.now() < run.ready_at:
            body = self._envelope(run.agent, run_id, "running")
        else:
            body = self._finished(run_id, run)
        return reply_from(run.agent, status, body)

    async def _called(self, op: str, run_id: str) -> None:
        self.calls.append((op, run_id))
        if self.before_call:
            await self.before_call(op, run_id)

    async def start(self, agent: str, request: Mapping[str, Any]) -> AgentReply:
        run_id = request["run_id"]
        await self._called("start", run_id)
        try:
            AGENTS[agent].request.model_validate(request)
        except ValidationError:
            return AgentReply(422)
        known = self.runs.get(run_id)
        if known:
            if known.body != request:
                return AgentReply(409)
            return self._answer(200, run_id, known)
        key = _key(agent, request)
        queue = self.scripts.get(key)
        script = queue.pop(0) if queue else None
        delay = script.after_seconds if script and script.kind == "late" else 0
        run = _Run(agent, key, request, self._stored(key), script, self.clock.now() + timedelta(seconds=delay))
        self.runs[run_id] = run
        if script and script.kind == "duplicate":
            return self._answer(200, run_id, run)
        return reply_from(agent, 202, self._envelope(agent, run_id, "queued"))

    async def poll(self, agent: str, run_id: str) -> AgentReply:
        await self._called("poll", run_id)
        run = self.runs.get(run_id)
        if run is None:
            return AgentReply(404)
        if run.script and run.script.kind == "404" and run.script.times > 0:
            run.script.times -= 1
            kept = run.script if run.script.times > 0 else None
            del self.runs[run_id]
            if kept:
                self.scripts.setdefault(run.key, []).insert(0, kept)
            return AgentReply(404)
        return self._answer(200, run_id, run)

    async def cancel(self, agent: str, run_id: str) -> AgentReply:
        await self._called("cancel", run_id)
        run = self.runs.get(run_id)
        if run is None:
            return AgentReply(404)
        run.cancelled = True
        return self._answer(200, run_id, run)


async def load_recorded_dossier(db: AsyncSession, tenant_id: str, root: Path = REPLAY_DIR) -> int:
    """Adds the pages the stored answers cite (case files and reference documents), and the modules a complete file needs. Synthetic, like the rest of the demo."""
    ctx = type("Writer", (), {"db": db, "tenant": Tenant(tenant_id, "simulation")})()
    added = 0
    for path in sorted((root / "dossier").glob("*.json")):
        if await db.get(ROW["case"], path.stem) is None:
            continue  # the file is for a case this database doesn't have
        data = json.loads(path.read_text())
        for doc in data["documents"]:
            STORES["dossier_document"].add(ctx, doc)
            added += 1
        await db.flush()
        for c in data["chunks"]:
            db.add(ROW["dossier_chunk"](
                id=c["id"], tenant_id=tenant_id, case_id=c["case_id"], document_id=c["document_id"], position=c["position"], page=c["page"],
                suspect=False, text=c["text"], text_hash="sha256:" + hashlib.sha256(c["text"].encode()).hexdigest(),
            ))
            added += 1
        for r in data.get("reference_pages", []):
            db.add(ROW["reference_page"](id=r["id"], tenant_id=tenant_id, basis_id=r["basis_id"], document_id=r["document_id"], version=r["version"], page=r["page"], text=r["text"]))
            added += 1
    await db.flush()
    return added
