"""What the runner must remember about one agent run. Kept apart from `agent_run`, which holds numbers and ids only."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

RunKind = Literal["reader", "check", "brief", "challenge"]


class RunWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent: str
    kind: RunKind
    case_id: str
    pipeline_run_id: str
    generation: int
    attempt: int
    check_id: Optional[str] = None
    result_id: Optional[str] = None
    request: dict  # the exact body, so a re-post sends the same id and body
    posted_at: Optional[datetime] = None
    next_poll_at: datetime
    deadline_at: datetime
    reposts: int = 0
    delivered: Optional[dict] = None  # the response that passed output control
    applied_at: Optional[datetime] = None
    outcome: Optional[str] = None  # how the delivered result was treated
