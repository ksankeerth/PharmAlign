"""Where a contract run meets the agent.

The contract routes (in `pharmalign_agent_common`) call `read_dossier` once per run, in the
background, and turn what it returns (or raises) into the run's status:

- returns a `RunOutcome`      -> completed, with the result
- raises `BudgetExceeded`     -> stopped_early, finish reason budget
- runs past the deadline      -> stopped_early, finish reason deadline
- raises `OutputTruncated`    -> failed, finish reason length
- raises `OutputInvalid`      -> failed, code invalid_output (after one retry)
- raises anything else        -> failed, code internal_error
"""
from google.adk.models.base_llm import BaseLlm
from pharmalign_agent_common import BudgetExceeded, RunOutcome, Settings, run_agent
from pharmalign_contracts.agents.run_agents import DossierReadRequest

__all__ = ["BudgetExceeded", "RunOutcome", "read_dossier"]


async def read_dossier(
    request: DossierReadRequest, *, settings: Settings | None = None, model: BaseLlm | None = None
) -> RunOutcome:
    """Runs the agent for one request. `model` is for tests; runs normally use the configured model."""
    from app.agent import SPEC

    return await run_agent(SPEC, request, settings=settings or Settings.from_env(), model=model)
