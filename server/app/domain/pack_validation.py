"""Pack validation and diff. Their results match the stored vectors in server/tests/vectors/packs.json. Pure functions over the pack document as plain JSON, so a draft
that does not parse as a `PackDocument` can still be checked and its errors listed."""
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from pharmalign_contracts.server.models import LOCKED_STEP_KINDS, PackChange, PackIssue, PackValidation

# JavaScript's \d and \b are ASCII only.
NUMBERS = re.compile(r"\d|\b(months?|days?|temperature|humidity|mg|ml|%)\b", re.I | re.A)
STEP_ORDER = ("door", "decide", "record")


def _filled(value: Any) -> bool:
    """JavaScript truthiness for a JSON value."""
    return value is not None and value is not False and value != 0 and value != ""


@dataclass(frozen=True)
class SourceCheck:
    """What the knowledge snapshot says about the pack's source clauses. `snapshot_id` is None when the workspace has no snapshot, so nothing could be checked.
    `unresolved` holds the (doc_id, clause) pairs that match no passage of that document."""

    snapshot_id: Optional[str]
    unresolved: frozenset[tuple[str, str]] = frozenset()


def clause_sources(doc: dict) -> Iterator[tuple[str, Optional[str], str, str]]:
    """Every source that names a document and a clause, as (path, check id, doc_id, clause). A check's path is `checks[ID]`; any other source gets its JSON path."""

    def walk(node: Any, path: str, check_id: Optional[str]) -> Iterator[tuple[str, Optional[str], str, str]]:
        if isinstance(node, dict):
            src = node.get("source")
            if isinstance(src, dict) and src.get("doc_id") and isinstance(src.get("clause"), str) and src["clause"].strip():
                yield (path if check_id and path == f"checks[{check_id}]" else f"{path}.source"), check_id, src["doc_id"], src["clause"]
            for key, value in node.items():
                if key != "source":
                    yield from walk(value, f"{path}.{key}" if path else key, check_id)
        elif isinstance(node, list):
            for item in node:
                key = item.get("id") if isinstance(item, dict) else None
                is_check = path == "checks" and key is not None
                yield from walk(item, f"{path}[{key}]" if key is not None else path, key if is_check else check_id)

    yield from walk(doc, "", None)


def validate_pack_document(doc: dict, known_doc_ids: Optional[set[str]] = None, source_check: Optional[SourceCheck] = None) -> PackValidation:
    issues: list[PackIssue] = []

    def add(severity: str, path: str, rule: str, message: str) -> None:
        issues.append(PackIssue(path=path, rule=rule, severity=severity, message=message))

    err = lambda path, rule, message: add("error", path, rule, message)  # noqa: E731
    warn = lambda path, rule, message: add("warning", path, rule, message)  # noqa: E731

    checks = doc.get("checks") or []
    ids = [c["id"] for c in checks]
    dup = [cid for i, cid in enumerate(ids) if ids.index(cid) != i]
    for cid in dict.fromkeys(dup):
        err(f"checks[{cid}]", "unique_check_ids", f"Check id {cid} is used more than once.")

    for c in checks:
        p = f"checks[{c['id']}]"
        cid, method, source = c["id"], c.get("method"), c.get("source")
        if source is None and not c.get("local_practice"):
            err(p, "source_required", f"{cid} needs a source, or must be marked local practice.")
        if method == "typed" and (not (c.get("extract") or []) or not _filled(c.get("rule"))):
            err(p, "typed_needs_rule", f"{cid} is typed, so it needs values to extract and a rule.")
        if method == "code" and not _filled(c.get("rule")):
            err(p, "code_needs_rule", f"{cid} is a code check, so it needs a rule.")
        if method == "agent" and (source is None or not _filled(c.get("profile"))):
            err(p, "agent_needs_source_and_profile", f"{cid} is an agent check, so it needs a source and a profile.")
        if c.get("legal_force") == "binding" and c.get("waive_by") == "case_officer":
            err(p, "binding_waiver", f"{cid} is binding law, so a case officer can't waive it.")
        if c.get("on_fail") == "draft_return_notice" and method != "code":
            err(p, "return_notice_code_only", f"Only a code check can draft a return notice ({cid}).")
        if method == "agent" and NUMBERS.search(c.get("question") or ""):
            warn(p, "prefer_typed", f"{cid} asks about numbers or units. A typed check lets code compare them.")
        if c.get("severity") == "blocking" and c.get("may_pass_alone") and method in ("agent", "typed"):
            warn(p, "blocking_passes_alone", f"{cid} is blocking and may pass without a person.")
        if source is not None and known_doc_ids is not None and source["doc_id"] not in known_doc_ids:
            err(p, "unknown_source", f"{cid} cites {source['doc_id']}, which is not in the library.")
        if c.get("local_practice"):
            warn(p, "local_practice", f"{cid} is local practice with no published source. It will be counted as a gap signal.")

    if not _filled(doc.get("final_escalation_holder")):
        err("final_escalation_holder", "escalation_holder_required", "Name a final escalation holder for urgent alerts.")

    for ct in doc.get("case_types") or []:
        fallbacks = [r for r in ct["routes"] if r.get("reliance_rule") is None]
        if len(fallbacks) > 1:
            err(f"case_types[{ct['id']}]", "one_fallback_route", f"{ct['label']} has {len(fallbacks)} fallback routes; at most one is allowed.")
        for r in ct["routes"]:
            p = f"case_types[{ct['id']}].routes[{r['id']}]"
            kinds = [s["kind"] for s in r["steps"]]
            for needed in STEP_ORDER:
                if needed not in kinds:
                    err(p, "locked_steps", f"{r['label']} must have a {needed} step.")
            if "door" in kinds and "decide" in kinds and kinds.index("door") > kinds.index("decide"):
                err(p, "door_before_decide", "A door check must come before the decision.")
            if "decide" in kinds and "record" in kinds and kinds.index("decide") > kinds.index("record"):
                err(p, "decide_before_record", "The record can only update after a signed decision.")
            step_ids = [s["id"] for s in r["steps"]]
            if len(set(step_ids)) != len(step_ids):
                err(p, "unique_step_ids", "Step ids must be unique within a route.")
            rule = r.get("reliance_rule")
            if rule and rule.get("source") is None and not rule.get("local_practice"):
                err(f"{p}.reliance_rule", "source_required", "A reliance rule needs a source, or must be marked local practice.")
            if rule and rule.get("local_practice") and r.get("status_note") != "proposed":
                warn(p, "label_proposed", f"{r['label']} has no published source. Label it proposed.")
            for s in r["steps"]:
                sp = f"{p}.steps[{s['id']}]"
                autonomy = s.get("agent_autonomy")
                if s["kind"] == "decide" and autonomy != "person_signs":
                    err(sp, "decision_person_signs", f"{s['name']}: a decision step always ends with a person signing.")
                if s["kind"] == "question" and autonomy in ("do_and_report", "person_signs"):
                    err(sp, "question_suggest_only", f"{s['name']}: an agent can only suggest what goes to a company.")
                missing = [i for i in (s.get("check_ids") or []) if i not in ids]
                if missing:
                    err(sp, "unknown_checks", f"{s['name']} refers to unknown checks: {', '.join(missing)}.")
    if source_check is not None:
        sources = [x for x in clause_sources(doc) if known_doc_ids is None or x[2] in known_doc_ids]
        if source_check.snapshot_id is None:
            if sources:
                warn("sources", "sources_unchecked", "Source clauses were not checked against the library: this workspace has no knowledge snapshot yet.")
        else:
            for path, check_id, doc_id, clause in sources:
                if (doc_id, clause) in source_check.unresolved:
                    who = f"Check {check_id}" if check_id else path
                    err(path, "source_clause_unresolved",
                        f'{who}: clause "{clause}" matches no passage of {doc_id} in knowledge snapshot {source_check.snapshot_id}. Point it at a real article or heading.')
    return PackValidation(valid=not any(i.severity == "error" for i in issues), issues=issues)


def locked_step_changes(before: dict, after: dict) -> list[str]:
    """What an edit does to the steps no one may change: a step marked `locked` stays, keeps its kind and stays locked.
    A step of a locked kind can't be added or made out of another. Only routes present in both are compared.
    A new route is checked for its required steps by validation."""
    problems: list[str] = []
    old_routes = {(ct["id"], r["id"]): r for ct in before.get("case_types") or [] for r in ct["routes"]}
    for ct in after.get("case_types") or []:
        for r in ct["routes"]:
            old = old_routes.get((ct["id"], r["id"]))
            if old is None:
                continue
            was = {s["id"]: s for s in old["steps"]}
            now = {s["id"]: s for s in r["steps"]}
            where = f"{ct['label']}, {r['label']}"
            for sid, step in was.items():
                kind = step["kind"]
                if step.get("locked"):
                    if sid not in now:
                        problems.append(f"{where}: the {kind} step can't be removed.")
                    elif now[sid]["kind"] != kind:
                        problems.append(f"{where}: the {kind} step keeps its kind.")
                    elif not now[sid].get("locked"):
                        problems.append(f"{where}: the {kind} step stays locked.")
                elif sid in now and now[sid]["kind"] in LOCKED_STEP_KINDS and now[sid]["kind"] != kind:
                    problems.append(f"{where}: a step can't be turned into a {now[sid]['kind']} step.")
            for sid, step in now.items():
                if sid not in was and step["kind"] in LOCKED_STEP_KINDS:
                    problems.append(f"{where}: a {step['kind']} step can't be added.")
    return problems


def misplaced_new_steps(before: dict, after: dict) -> list[str]:
    """A step added to a route goes before the decision."""
    problems: list[str] = []
    old_routes = {(ct["id"], r["id"]): {s["id"] for s in r["steps"]} for ct in before.get("case_types") or [] for r in ct["routes"]}
    for ct in after.get("case_types") or []:
        for r in ct["routes"]:
            old = old_routes.get((ct["id"], r["id"]))
            kinds = [s["kind"] for s in r["steps"]]
            if old is None or "decide" not in kinds:
                continue
            decide = kinds.index("decide")
            for i, s in enumerate(r["steps"]):
                if s["id"] not in old and i > decide:
                    problems.append(f"{ct['label']}, {r['label']}: the new step {s['name']} must go before the decision.")
    return problems


# ------------------------------------------------------------------ diff

def _is_obj(v: Any) -> bool:
    return isinstance(v, dict)


def _keyed(arr: list) -> Optional[dict]:
    if not all(_is_obj(x) and isinstance(x.get("id"), str) for x in arr):
        return None
    return {x["id"]: x for x in arr}


_MISSING = object()


def _diff(before: Any, after: Any, path: str) -> list[dict]:
    if before is _MISSING and after is _MISSING:
        return []
    if isinstance(before, list) and isinstance(after, list):
        a, b = _keyed(before), _keyed(after)
        if a is not None and b is not None:
            out: list[dict] = []
            for key, v in a.items():
                out.extend(_diff(v, b.get(key, _MISSING), f"{path}[{key}]"))
            for key, v in b.items():
                if key not in a:
                    out.append({"path": f"{path}[{key}]", "before": None, "after": v})
            return out
        if json.dumps(before, sort_keys=False) == json.dumps(after, sort_keys=False):
            return []
        return [{"path": path or "(root)", "before": before, "after": after}]
    if _is_obj(before) and _is_obj(after):
        out = []
        for k in dict.fromkeys([*before, *after]):
            out.extend(_diff(before.get(k, _MISSING), after.get(k, _MISSING), f"{path}.{k}" if path else k))
        return out
    if before is _MISSING:
        return [{"path": path or "(root)", "before": None, "after": after}]
    if after is _MISSING:
        return [{"path": path or "(root)", "before": before, "after": None}]
    if before == after and isinstance(before, bool) == isinstance(after, bool):
        return []
    return [{"path": path or "(root)", "before": before, "after": after}]


def json_diff(before: Any, after: Any) -> list[PackChange]:
    return [PackChange(**c) for c in _diff(before, after, "")]
