"""A fake MCP tool server that behaves like the PharmAlign server's tools for the Gap Analyst (server/app/mcp/tools.py).

The knowledge tools (`search_kb`, `read_clause`, `verify_quote`) are the Checklist Assessor's golden copies, imported from
`agents/checklist-assessor/samples/golden/server_like.py`, so both harnesses share one copy of the server's search, clause and quote rules.
A gap run has no case, so it reads the whole snapshot, as the server does for a run with no case.

Added here, copied from server/app/mcp/tools.py (`check_fidelity.py` compares them with the server's source):
- `get_gap_signals(kinds)`: the workspace's signals, most counted first, then by id, at most 50, with `more`;
- `get_pack_rule(check_id)`: without an id, the active pack's checks (`pack_version_id`, `version`, `check_id`, `question`, `stage`, `method`),
  in check id order; with one, `{pack_version_id, version, rule}` where `rule` is the whole check, or `not_found`.

What is different from the server: the data is a scenario file, not the agent_read views. There is no run token, no run scope and
no tool log in the database.
"""
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mcp import types
from pydantic import Field, ValidationError

HERE = Path(__file__).resolve().parent
ASSESSOR = HERE.parents[2] / "checklist-assessor" / "samples" / "golden" / "server_like.py"


def _load_assessor():
    spec = importlib.util.spec_from_file_location("assessor_server_like", ASSESSOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["assessor_server_like"] = module
    spec.loader.exec_module(module)
    return module


base = _load_assessor()
Args, ToolFailure, Law, World, Dossier = base.Args, base.ToolFailure, base.Law, base.World, base.Dossier
MAX_ROWS = base.MAX_ROWS
PACK_VERSION = "pv-ae-3"

# ---- copied from server/app/mcp/tools.py


class GapSignals(Args):
    kinds: Optional[list[str]] = Field(None, max_length=10)


class PackRule(Args):
    check_id: Optional[str] = Field(None, max_length=128, description="One check. Without it, the list of checks.")


DESCRIPTIONS = {
    "get_gap_signals": "Lists the gap signals counted by code: empty slots, questions with no source, local practice rules, repeated waivers and overrides.",
    "get_pack_rule": "Reads a check from the country pack: the case's pinned pack, or the active pack when there is no case.",
    "search_kb": base.DESCRIPTIONS["search_kb"],
    "read_clause": base.DESCRIPTIONS["read_clause"],
    "verify_quote": base.DESCRIPTIONS["verify_quote"],
}
ARGS = {"get_gap_signals": GapSignals, "get_pack_rule": PackRule, "search_kb": base.SearchKb, "read_clause": base.ReadClause, "verify_quote": base.VerifyQuote}


@dataclass
class GapWorld:
    """One scenario as the tool server sees it: the workspace's signals, the active pack's checks and the snapshot's law."""

    signals: list[dict]
    checks: list[dict]  # whole check documents, each with an `id`
    law: Law
    world: World = field(init=False)

    def __post_init__(self) -> None:
        self.world = World(Dossier([]), self.law, [], None, None)  # no case: the whole snapshot


def get_gap_signals(w: GapWorld, a: GapSignals) -> dict:
    rows = sorted(({"id": s["id"], "kind": s["kind"], "detail": s["detail"], "count": s.get("count", 1)} for s in w.signals),
                  key=lambda r: (-r["count"], r["id"]))
    found = [r for r in rows if not a.kinds or r["kind"] in a.kinds]
    shown, more = base._capped(found, MAX_ROWS)
    return {"signals": shown, "more": more}


def get_pack_rule(w: GapWorld, a: PackRule) -> dict:
    if a.check_id:
        rows = [c for c in w.checks if c["id"] == a.check_id]
        if not rows:
            raise ToolFailure("not_found", f"No check {a.check_id} in this pack.")
        return {"pack_version_id": PACK_VERSION, "version": rows[0].get("version", 1), "rule": rows[0]}
    rows = [{"pack_version_id": PACK_VERSION, "version": c.get("version", 1), "check_id": c["id"], "question": c.get("question"), "stage": c.get("stage"),
             "method": c.get("method")} for c in sorted(w.checks, key=lambda c: c["id"])]
    shown, more = base._capped(rows, MAX_ROWS)
    return {"checks": shown, "more": more}


RUN = {
    "get_gap_signals": get_gap_signals,
    "get_pack_rule": get_pack_rule,
    "search_kb": lambda w, a: base.search_kb(w.world, a),
    "read_clause": lambda w, a: base.read_clause(w.world, a),
    "verify_quote": lambda w, a: base.verify_quote(w.world, a),
}


class GapServerLikeMcp(base.ServerLikeMcp):
    """The Assessor's server-like transport and log, with the Gap Analyst's five tools."""

    def __init__(self, gap_world: GapWorld):
        super().__init__(gap_world.world)
        self.gap_world = gap_world

    async def _list(self, req: types.ListToolsRequest) -> types.ServerResult:
        tools = [types.Tool(name=n, description=DESCRIPTIONS[n], inputSchema=base.schema(ARGS[n])) for n in RUN]
        return types.ServerResult(types.ListToolsResult(tools=tools))

    def _answer(self, name: str, arguments: dict) -> tuple[str, dict]:
        if name not in RUN:
            return "refused:not_allowed", base.error_body(name, "not_allowed", f"{name[:64]} is not one of this agent's tools.")
        try:
            args = ARGS[name].model_validate(arguments)
        except ValidationError as e:
            return "error:invalid_arguments", base.error_body(name, "invalid_arguments", f"Invalid arguments: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        try:
            return "ok", base.fit(RUN[name](self.gap_world, args), base.MAX_CHARS)
        except ToolFailure as f:
            return f"error:{f.code}", base.error_body(name, f.code, f.message, f.retryable)


def check_fidelity() -> list[str]:
    """Differences between the copies above and server/app/mcp/tools.py. Empty when they match."""
    source = (base.SERVER_APP / "mcp" / "tools.py").read_text()
    problems = [f"{name}: the description differs from the server's" for name in ("get_gap_signals", "get_pack_rule")
                if DESCRIPTIONS[name] not in source]  # the knowledge tools are checked by the Assessor's check_fidelity.py
    for needle in ("ORDER BY count DESC, id", "kinds: Optional[list[str]] = Field(None, max_length=10)",
                   'check_id: Optional[str] = Field(None, max_length=128, description="One check. Without it, the list of checks.")',
                   'rule ->> \'question\' AS question, rule ->> \'stage\' AS stage, rule ->> \'method\' AS method',
                   'return {"pack_version_id": rows[0]["pack_version_id"], "version": rows[0]["version"], "rule": rows[0]["rule"]}',
                   'raise ToolFailure("not_found", f"No check {a.check_id} in this pack.")', 'return {"signals": shown, "more": more}',
                   'return {"checks": shown, "more": more}'):
        if needle not in source:
            problems.append(f"the server's tools.py no longer has: {needle[:80]}")
    return problems


if __name__ == "__main__":
    found = check_fidelity()
    print("\n".join(found) or "The gap tools match the server's source.")
    sys.exit(1 if found else 0)
