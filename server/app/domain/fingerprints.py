"""Fingerprints. The same inputs give the same fingerprint as the stored vectors in server/tests/vectors/guards.json.

check fingerprint: one check's evidence (generation, attempt, quotes, extracted values, rule outcome, challenge)
case input: every current check's status and fingerprint, plus valid waivers and confirmations
case package: case input + current recommendation + pinned pack and snapshot + sequence + open foreign matches
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from pharmalign_contracts.server.models import (
    CaseDetail,
    CheckDefinition,
    CheckDetail,
    Conflict,
    Promotion,
    Question,
    RecommendationVersion,
    Thread,
    WatchMatch,
)

MAX_SAFE_INTEGER = 2**53
LONE_SURROGATE = re.compile("[\ud800-\udfff]")
# The order ICU's default collation gives these, which is what localeCompare uses.
ASCII_ORDER = "\t\n\x0b\x0c\r _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789"


@dataclass(frozen=True)
class CaseFacts:
    """Everything the guards read about one case. Handlers load it from the database."""

    case_id: str
    case: Optional[CaseDetail] = None
    assessors: Sequence[str] = ()
    promotion: Optional[Promotion] = None
    checks: Sequence[CheckDetail] = ()
    recommendations: Sequence[RecommendationVersion] = ()
    threads: Sequence[Thread] = ()
    questions: Sequence[Question] = ()
    matches: Sequence[WatchMatch] = ()
    conflicts: Sequence[Conflict] = ()
    pack_checks: Sequence[CheckDefinition] = ()


def _js_number(n: float) -> str:
    """A number as JSON.stringify writes it. 5.0 is "5", 1e-7 is "1e-7", 1e20 is written out in full."""
    if isinstance(n, int):
        if abs(n) <= MAX_SAFE_INTEGER:
            return str(n)
        n = float(n)
    if not math.isfinite(n):
        return "null"
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(n))).as_tuple()
    point = len(digit_tuple) + exponent
    digits = "".join(map(str, digit_tuple)).lstrip("0")
    point -= len(digit_tuple) - len(digits)
    digits = digits.rstrip("0")
    size = len(digits)
    if size <= point <= 21:
        return f"{sign}{digits}{'0' * (point - size)}"
    if 0 < point <= 21:
        return f"{sign}{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    power = point - 1
    mantissa = digits if size == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{sign}{mantissa}e{'+' if power >= 0 else '-'}{abs(power)}"


def _js_string(text: str) -> str:
    return LONE_SURROGATE.sub(lambda m: f"\\u{ord(m.group()):04x}", json.dumps(text, ensure_ascii=False))


def _js_sort_key(text: str) -> bytes:
    """JavaScript sorts strings by UTF-16 code unit, not by code point."""
    return text.encode("utf-16-be", "surrogatepass")


def locale_sort_key(text: str) -> tuple:
    """localeCompare order for ids: case only breaks ties, and lower case comes first."""
    def primary(ch: str) -> tuple[int, int]:
        at = ASCII_ORDER.find(ch)
        if at >= 0:
            return (0, at)
        if ch.isascii() and ch.isalpha():
            return (1, ord(ch.lower()))
        return (2, ord(ch))
    return (tuple(primary(ch) for ch in text), tuple(ch.isupper() for ch in text))


def stable_stringify(value: Any) -> str:
    """Stable JSON: object keys sorted, so equal values always hash the same."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_stringify(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_js_sort_key)
        return "{" + ",".join(f"{_js_string(k)}:{stable_stringify(value[k])}" for k in keys) + "}"
    raise TypeError(f"Can't fingerprint a {type(value).__name__}")


def fingerprint_of(value: Any) -> str:
    """A contract fingerprint (`sha256:<hex>`) of any JSON value."""
    return "sha256:" + hashlib.sha256(stable_stringify(value).encode("utf-8")).hexdigest()


def check_fingerprint(c: CheckDetail) -> str:
    return fingerprint_of({
        "id": c.id,
        "check_id": c.check_id,
        "generation": c.generation,
        "attempt": c.attempt,
        "evidence": [e.model_dump(mode="json") for e in c.evidence],
        "extracted": [f.model_dump(mode="json") for f in c.extracted],
        "rule_outcome": c.rule_outcome.model_dump(mode="json") if c.rule_outcome else None,
        "challenge": {
            "verdict": c.challenge.verdict,
            "evidence": [e.model_dump(mode="json") for e in c.challenge.evidence],
            "counted": c.challenge.counted,
        } if c.challenge else None,
        "suspect_pages": list(c.suspect_pages),
        "no_source_found": c.no_source_found,
    })


def case_checks(facts: CaseFacts) -> list[CheckDetail]:
    """Current check results of a case, ordered by check id. Replaced results stay in the table but never count."""
    current = [c for c in facts.checks if c.case_id == facts.case_id and c.is_current]
    return sorted(current, key=lambda c: locale_sort_key(c.check_id))


def case_input_fingerprint(facts: CaseFacts) -> str:
    return fingerprint_of([
        {
            "check": c.check_id,
            "status": c.status,
            "fingerprint": c.fingerprint,
            "waiver": c.waiver.id if c.waiver and c.waiver.valid else None,
            "confirmation": c.confirmation.id if c.confirmation and c.confirmation.valid else None,
        }
        for c in case_checks(facts)
    ])


def current_recommendation(facts: CaseFacts) -> Optional[RecommendationVersion]:
    return next((r for r in facts.recommendations if r.is_current), None)


def open_case_matches(facts: CaseFacts) -> list[WatchMatch]:
    return [m for m in facts.matches if m.status == "open" and m.target_type == "case" and m.target_id == facts.case_id]


def case_package_fingerprint(facts: CaseFacts) -> str:
    case = facts.case
    current = current_recommendation(facts)
    return fingerprint_of({
        "case": facts.case_id,
        "sequence": case.sequence if case else 0,
        "pack": case.pack_version_id if case else None,
        "snapshot": case.kb_snapshot_id if case else None,
        "input": case_input_fingerprint(facts),
        "recommendation": {"version": current.version, "input": current.input_fingerprint} if current else None,
        "foreign_matches": sorted((m.id for m in open_case_matches(facts)), key=_js_sort_key),
    })
