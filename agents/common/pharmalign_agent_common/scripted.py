"""Scripted model mode: a model that follows a recorded script, for running the whole chain with no model key.

`PHARMALIGN_MODEL_MODE=scripted`. For a request, the model makes the script's real MCP tool calls (so anchors and quotes
come from real tool results and pass the agent's own rules), then answers with the recorded answer.

Scripts are data under demo/scripted/<agent>/<key>.json:
    {"case": "CASE-1042",                     runs only: the case the script is for
     "calls": [{"tool": "read_page", "args": {"doc_id": "d-1042-27", "page": 1}}],
     "answer": {"from": "replay/runs/CASE-1042/LEGAL-01.json", "patch": {"fields.2.agent_view": null}}}   or an inline object
`patch` sets values in the recorded answer by dotted path (a number steps into a list), where the agent's rules ask for more.
A session script (Knowledge Assistant) also has "question": text the person's question contains.
In an answer, a string like "$request.input_fingerprint" echoes that field of the request.

A request with no script of its own gives the recorded answer the server's replay uses for it (demo/replay/runs/<case>/<key>.json), and the
tool calls are the ones that read each anchor in that answer. A request with neither fails the run at once (NoScript).
It is never allowed in live mode.
"""
import json
import os
import re
from pathlib import Path
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from .testing.fake_model import call_tool, say_json

RETRY_PREFIX = "Your answer was rejected"
NO_SCRIPT = "no script for this request"


class NoScript(RuntimeError):
    """The request has no script."""


class ScriptedInLive(RuntimeError):
    """Scripted mode was asked to serve a live request."""


def demo_dir() -> Path:
    """PHARMALIGN_DEMO_DIR, else the nearest `demo` folder above this file or the working directory."""
    if env := os.environ.get("PHARMALIGN_DEMO_DIR"):
        return Path(env)
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in start.parents:
            if (parent / "demo" / "scripted").is_dir():
                return parent / "demo"
    return Path("demo")


def refuse_live(model_mode: str, mode: str) -> str | None:
    """Why this request is refused, or None. Scripted answers are for simulation only."""
    if model_mode == "scripted" and mode == "live":
        return "The scripted model is never used for live requests."
    return None


def fill(value: Any, request: Any) -> Any:
    """Replaces "$request.a.b.0" strings with that field of the request."""
    if isinstance(value, str) and value.startswith("$request."):
        found: Any = request
        for step in value.removeprefix("$request.").split("."):
            found = found[int(step)] if isinstance(found, list) else found[step]
        return found
    if isinstance(value, dict):
        return {k: fill(v, request) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, request) for v in value]
    return value


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _set_path(value: Any, path: str, new: Any) -> None:
    *steps, last = path.split(".")
    for step in steps:
        value = value[int(step)] if isinstance(value, list) else value[step]
    if isinstance(value, list):
        value[int(last)] = new
    else:
        value[last] = new


def _answer_of(script: dict, root: Path) -> Any:
    answer = script["answer"]
    if isinstance(answer, dict) and "from" in answer:
        recorded = _read(root / answer["from"])
        for path, new in answer.get("patch", {}).items():
            _set_path(recorded, path, new)
        return recorded
    return answer


class Script:
    def __init__(self, calls: list[dict], answer: Any):
        self.calls, self.answer = calls, answer


def run_key(agent: str, request: dict) -> str:
    """The file name for a run: the check for the assessor, the finding for the challenger, the agent otherwise."""
    if agent == "challenger":
        return "challenge-" + request["target"]["finding_id"]
    if agent == "checklist-assessor":
        return request["check"]["id"]
    return agent


def _anchors(value: Any, found: dict[str, None]) -> dict[str, None]:
    """Every anchor an answer cites, once, in order."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("anchor", "text_anchor") and isinstance(item, str):
                found.setdefault(item)
            else:
                _anchors(item, found)
    elif isinstance(value, list):
        for item in value:
            _anchors(item, found)
    return found


def _call_for(anchor: str) -> dict:
    """The tool call that returns the text at an anchor: a page of a dossier file, or a clause of a document."""
    page = re.fullmatch(r"(d-[\w-]+):v\d+:p(\d+)", anchor)
    return {"tool": "read_page", "args": {"doc_id": page[1], "page": int(page[2])}} if page else {"tool": "read_clause", "args": {"anchor": anchor}}


def script_for_run(agent: str, request: Any, root: Path | None = None) -> Script:
    """The script for a run request, else the recorded answer for it. Raises NoScript."""
    body = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
    demo = root or demo_dir()
    key, case = run_key(agent, body), body["case"]["case_id"]
    path = demo / "scripted" / agent / f"{key}.json"
    script = _read(path) if path.is_file() else None
    if script is not None and script.get("case") == case:
        return Script(script["calls"], fill(_answer_of(script, demo), body))
    recorded = demo / "replay" / "runs" / case / f"{key}.json"
    if not recorded.is_file():
        raise NoScript(f"{NO_SCRIPT}: {agent} {key} on {case}, and no recorded answer")
    answer = fill(_read(recorded), body)
    return Script([_call_for(a) for a in _anchors(answer, {})], answer)


def question_scripts(agent: str, root: Path | None = None) -> list[tuple[str, Script]]:
    demo = root or demo_dir()
    found = []
    for path in sorted((demo / "scripted" / agent).glob("*.json")):
        script = _read(path)
        if "question" in script:
            found.append((script["question"].casefold(), Script(script["calls"], _answer_of(script, demo))))
    return found


class ScriptedModel(BaseLlm):
    """Makes the script's tool calls one by one, then gives the answer as JSON.

    `script` is fixed for a run. A session sets `agent` instead, and the script is picked from the person's question."""

    model: str = "scripted"
    script: Any = None
    agent: str | None = None

    def _pick(self, question: str) -> Script:
        if self.script is not None:
            return self.script
        if self.agent:
            for text, script in question_scripts(self.agent):
                if text in question.casefold():
                    return script
        raise NoScript(NO_SCRIPT)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        asked = [(i, "".join(p.text or "" for p in c.parts or [])) for i, c in enumerate(llm_request.contents or [])
                 if c.role == "user" and any(p.text for p in c.parts or [])]
        real = [a for a in asked if not a[1].startswith(RETRY_PREFIX)]
        if not real:
            raise NoScript(NO_SCRIPT)
        start, question = real[-1]
        script = self._pick(question)
        done = sum(1 for c in llm_request.contents[start + 1:] for p in c.parts or [] if p.function_response)
        retry = asked[-1][1].startswith(RETRY_PREFIX)
        if done < len(script.calls) and not retry:
            step = script.calls[done]
            yield call_tool(step["tool"], **step["args"])
        else:
            yield say_json(script.answer)


def model_for_run(agent: str, request: Any) -> ScriptedModel:
    return ScriptedModel(script=script_for_run(agent, request))


def model_for_session(agent: str) -> ScriptedModel:
    return ScriptedModel(agent=agent)


def unused_model() -> ScriptedModel:
    """For ADK's own dev routes, which have no request. It fails if it is asked to answer."""
    return ScriptedModel()


__all__ = ["NoScript", "ScriptedInLive", "ScriptedModel", "demo_dir", "model_for_run", "model_for_session", "refuse_live", "script_for_run"]
