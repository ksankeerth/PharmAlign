"""The nine setup stages, in order, and what done means for each."""
from typing import get_args

from pharmalign_contracts.server.models import SetupStage

DONE_WHEN = {
    "workspace": "Country, languages, day counting and data location are set.",
    "library": "Every must-have slot is filled with an approved version.",
    "rules_and_workflows": "A pack version validates with no errors.",
    "watch_sources": "Each trusted regulator has a healthy source.",
    "connections": "A connection passes conformance and is approved.",
    "people_and_notifications": "Every step role has a person, and urgent alerts have an escalation holder.",
    "agents_and_limits": "Agents are healthy and budgets are set for this data class.",
    "test_and_simulate": "All scenarios run and meet their expected outcome.",
    "sign_off": "A named Process Owner approves the pack, snapshot and mode.",
}
SETUP_STAGES: list[tuple[str, str]] = [(stage, DONE_WHEN[stage]) for stage in get_args(SetupStage)]
# A stage that can stay open without holding up sign-off.
OPTIONAL_STAGES = frozenset({"watch_sources"})
