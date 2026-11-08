#!/usr/bin/env python3
"""Checks that the fake tools still match the server's tools. No model, no network.

    cd agents/checklist-assessor && .venv/bin/python samples/golden/check_fidelity.py

The harness copies a few pieces from the server (tool arguments, descriptions, the search word rules, the clause label rule, the
arithmetic). This compares each copy with the server's source and lists any that has drifted. The citation check, the size limiter and
the typed-rule engine are not copied: `server_like.py` loads the server's own files.

Tool behaviour that needs the database (the ranking of `search_kb`, for one) can't be compared this way. README.md lists those
differences.
"""
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server_like as fake  # noqa: E402

SERVER = fake.SERVER_APP
TOOLS = tuple(fake.RUN)


def top_level(path: Path) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def same(a: ast.AST, b: ast.AST) -> bool:
    return ast.dump(a) == ast.dump(b)


def description(fn: ast.AST) -> str | None:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tool" and d.args and isinstance(d.args[0], ast.Constant):
            return d.args[0].value
    return None


def main() -> int:
    mine = top_level(HERE / "server_like.py")
    tools = top_level(SERVER / "mcp" / "tools.py")
    embedding = top_level(SERVER / "knowledge" / "embedding.py")
    search = top_level(SERVER / "knowledge" / "search.py")
    config = {n.target.id: n for c in top_level(SERVER / "config.py").values() if isinstance(c, ast.ClassDef) and c.name == "Settings"
              for n in c.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    problems: list[str] = []

    def compare(name: str, theirs: dict[str, ast.AST], where: str):
        if name not in theirs:
            problems.append(f"{name}: not found in {where}")
        elif not same(mine[name], theirs[name]):
            problems.append(f"{name}: differs from {where}")

    for name in ("Args", "DocId", "PageArgs", "TableArgs", "SearchDossier", "VerifyQuote", "SearchKb", "ReadClause", "DocStatus", "LookupTerm", "GetFacts",
                 "Compute", "SUSPECT_NOTE", "CELL_SPLIT", "CLAUSE_COLUMNS", "OPS", "FUNCS", "_eval", "ToolFailure", "_fact"):
        compare(name, tools, "server/app/mcp/tools.py")
    for name in ("STOPWORDS", "_TOKEN", "_AR_MARKS", "_AR_FOLD", "_stem", "terms"):
        compare(name, embedding, "server/app/knowledge/embedding.py")
    for name in ("like_contains", "clause_matches"):
        compare(name, search, "server/app/knowledge/search.py")
    for tool in TOOLS:
        if tool not in tools:
            problems.append(f"{tool}: not a server tool any more")
        elif description(tools[tool]) != fake.DESCRIPTIONS[tool]:
            problems.append(f"{tool}: description differs from the server's")
    for const, setting in (("MAX_ROWS", "mcp_max_rows"), ("MAX_CHARS", "mcp_max_chars")):
        theirs = config.get(setting)
        value = theirs.value.value if theirs is not None and isinstance(theirs.value, ast.Constant) else None
        if value != getattr(fake, const):
            problems.append(f"{const}: harness has {getattr(fake, const)}, server default {setting} is {value}")
    # The reach of a check: the server's rule is `check_documents` in mcp/server.py. Look for its three ingredients.
    server_text = (SERVER / "mcp" / "server.py").read_text()
    for needle in ('request.get("moved_source") or check.get("source")', 'check.get("supporting_sources")', "SCOPED_AGENTS = frozenset({\"checklist-assessor\"})"):
        if needle not in server_text:
            problems.append(f"the way a check's reach is set has changed in server/app/mcp/server.py (missing: {needle})")
    from pharmalign_contracts.agents.run_agents import RUN_AGENTS

    assessor = next(a for a in RUN_AGENTS if a.name == "checklist-assessor")
    if set(assessor.tools) != set(TOOLS):
        problems.append(f"the Assessor's contract tools are {sorted(assessor.tools)}, the harness serves {sorted(TOOLS)}")
    for p in problems:
        print("DRIFT ", p)
    print("fake tools match the server" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
