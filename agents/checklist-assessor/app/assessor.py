"""Where a contract run meets the agent. The contract routes call `assess` once per run, in the
background, and turn what it returns or raises into the run's status (see agents/common)."""
from google.adk.models.base_llm import BaseLlm
from pharmalign_agent_common import RunOutcome, Settings, run_agent
from pharmalign_contracts.agents.run_agents import AssessmentRequest


async def assess(
    request: AssessmentRequest, *, settings: Settings | None = None, model: BaseLlm | None = None
) -> RunOutcome:
    """Assesses one check. `model` is for tests; runs normally use the configured model."""
    from app.agent import SPEC

    return await run_agent(SPEC, request, settings=settings or Settings.from_env(), model=model)
