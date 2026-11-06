"""Rules of the foreign watch that need no database: what a notice is about, how serious, and what it matches."""
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from pharmalign_contracts.server import models as m

from ..domain.fingerprints import fingerprint_of

DEFAULT_SEVERITIES = ("info", "review", "urgent")
BURST = 3  # licences matched by one notice that make an incident


def change_type_of(text: str) -> str:
    t = text.lower()
    for word, kind in (("suspen", "suspension"), ("withdraw", "withdrawal"), ("recall", "recall"), ("label", "label_change"), ("warning", "label_change"),
                       ("gmp", "gmp_noncompliance"), ("manufacturing practice", "gmp_noncompliance")):
        if word in t:
            return kind
    return "other"


def notice_key(regulator: str, title: str, reference: Optional[str] = None) -> str:
    """Who a notice is, so a correction finds the notice it corrects. The regulator's own reference wins; without one, the title decides."""
    who = {"regulator": regulator.strip().lower()}
    if reference and reference.strip():
        return fingerprint_of({**who, "reference": " ".join(reference.lower().split())})
    return fingerprint_of({**who, "title": " ".join(title.lower().split())})


def content_hash(text: str) -> str:
    return fingerprint_of({"text": text})


def is_retraction(text: str) -> bool:
    return "retract" in text.lower()


def floor_for(pack: Optional[m.PackDocument], change_type: str) -> str:
    """The least severity the pack allows for this kind of change. 'review' when the pack says nothing."""
    found = next((f.floor for f in pack.severity_floors if f.change_type == change_type), None) if pack else None
    return found or "review"


def severities_of(pack: Optional[m.PackDocument]) -> Sequence[str]:
    return tuple(pack.severity_options) if pack and pack.severity_options else DEFAULT_SEVERITIES


def raised(options: Sequence[str], current: str, other: str) -> str:
    """The higher of two severities. A word the pack doesn't know ranks lowest, so it can't lower anything."""
    rank = {s: i for i, s in enumerate(options)}
    return other if rank.get(other, -1) > rank.get(current, -1) else current


@dataclass(frozen=True)
class Hit:
    target_type: str
    target_id: str
    basis: tuple[str, ...]
    confidence: str


def site_of(title: str) -> Optional[str]:
    found = re.search(r":\s*([^:]+)$", title)
    return found.group(1).strip() if found else None


def find_hits(
    regulator: str, title: str, text: str, medicines: Iterable[m.Medicine], licences: Iterable[m.Licence], open_cases: Iterable[m.CaseDetail],
    reliance: Iterable[m.RelianceBasis],
) -> list[Hit]:
    """What a notice is about: the medicines whose substance it names, then their licences and open cases. A notice that
    relies on the same regulator for a target, or names the strength, is a closer match."""
    said = f"{title} {text}".lower()
    licences, open_cases, reliance = list(licences), list(open_cases), list(reliance)
    hits: list[Hit] = []
    for med in medicines:
        if not any(s.split(" ")[0].lower() in said for s in med.substances):
            continue
        strength = bool(med.strength) and (med.strength.split(" ")[0] + " mg" in said or med.strength.lower() in said)
        basis = (f"substance: {' / '.join(med.substances)}", *((f"strength: {med.strength}",) if strength else ()))
        targets = [("licence", l.id) for l in licences if l.medicine_id == med.id] + [("case", c.id) for c in open_cases if c.medicine_id == med.id]
        for kind, target_id in targets:
            relies = any(r.target_type == kind and r.target_id == target_id and r.regulator == regulator for r in reliance)
            hits.append(Hit(
                kind, target_id, (*basis, f"relied on {regulator}") if relies else basis,
                "high" if relies and strength else "medium" if strength else "low",
            ))
    return hits
