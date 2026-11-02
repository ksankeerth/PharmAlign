"""Where a contract run meets the agent. This is the function you build.

`app/contract/api.py` calls `read_dossier` once per run, in the background, and turns what it
returns (or raises) into the run's status:

- returns a `ReadOutcome`              -> `completed`, with the result
- raises `BudgetExceeded`              -> `stopped_early`, finish reason `budget`
- runs past `budget.deadline_seconds`  -> `stopped_early`, finish reason `deadline` (handled for you)
- raises `NotImplementedError`         -> `failed`, code `not_implemented` (what happens today)
- returns a result that breaks a rule  -> `failed`, code `invalid_output`
- raises anything else                 -> `failed`, code `internal_error`

A suggested shape, which AGENT.md explains:

1. Put the run's documents where the tools can find them (session state is a good place).
2. Run `root_agent` from `app/agent.py` with a message built from the request's fact keys and modules.
3. Parse the agent's final answer into `DossierReadResult` and return it with the usage you counted.
"""
from dataclasses import dataclass, field

from app.contract.models import (
    DossierReadRequest,
    DossierReadResult,
    ModelCall,
    ToolError,
    Usage,
)


class BudgetExceeded(Exception):
    """Raise when the run would go past `budget.max_model_calls` or `budget.max_tool_calls`."""


@dataclass
class ReadOutcome:
    result: DossierReadResult
    usage: Usage | None = None
    model_calls: list[ModelCall] = field(default_factory=list)
    tool_errors: list[ToolError] = field(default_factory=list)


async def read_dossier(request: DossierReadRequest) -> ReadOutcome:
    raise NotImplementedError("The Dossier Reader isn't built yet. See AGENT.md, 'Your task'.")
