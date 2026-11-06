"""With PHARMALIGN_TWO_ROLES on, the five views act as two roles. Anyone in a group can use that group's operations.
Each person keeps their view, and separation-of-duty guards still apply to the person."""
from typing import Optional

ROLE_GROUPS = (("case_officer", "decision_maker"), ("platform_admin", "process_owner", "auditor"))


def group_of(view: Optional[str]) -> tuple[str, ...]:
    return next((g for g in ROLE_GROUPS if view in g), ())


def same_group(a: Optional[str], b: Optional[str]) -> bool:
    return b in group_of(a)
