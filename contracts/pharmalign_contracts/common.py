"""Models shared by every agent contract."""
from datetime import datetime
from typing import Annotated, Generic, Literal, Optional, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.5.0"
CONTRACT_HEADER = "X-PharmAlign-Contract"

RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$"
RunId = Annotated[str, Field(pattern=RUN_ID_PATTERN, description="Unique per request body.")]
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Language = Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$", examples=["en", "ar"])]
Fingerprint = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{4,64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
LongText = Annotated[str, Field(min_length=1, max_length=8000)]
Anchor = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$",
        description="Stable handle returned by a tool: document, version, position. Never written by hand.",
        examples=["E-S25:v3:c0412", "d-17:v1:p41"],
    ),
]


class Strict(BaseModel):
    """Every contract model refuses unknown fields."""

    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    """A quote, tied to the anchor a tool returned. The caller verifies it against the stored text."""

    anchor: Anchor
    quote: Annotated[str, Field(min_length=1, max_length=2000)]


class ExecutionProfile(Strict):
    model_set: Slug = Field(description="Which approved model list to use.")
    gate: Literal["on", "off"] = Field(
        "on", description="`off` removes knowledge and source rules, for comparison runs in simulation only."
    )
    data_class: Literal["public", "confidential", "synthetic"] = Field(
        description="Decides which models are allowed. Fallback never leaves this class's approved list."
    )


class Budget(Strict):
    max_model_calls: Annotated[int, Field(ge=1, le=200)]
    max_tool_calls: Annotated[int, Field(ge=0, le=500)]
    deadline_seconds: Annotated[int, Field(ge=5, le=3600)]


class Versions(Strict):
    pack: Identifier = Field(description="Pinned country pack version.")
    kb_snapshot: Identifier = Field(description="Pinned knowledge snapshot. Tools read only this.")


class Rejection(Strict):
    """Why the caller refused the previous attempt."""

    rule: Slug
    message: ShortText


class RunRequest(Strict):
    """Fields on every start request."""

    run_id: RunId
    run_token: Annotated[str, Field(min_length=8, description="Signed by the caller. Scopes tools to this run.")]
    tenant: Slug
    mode: Literal["live", "simulation"]
    execution_profile: ExecutionProfile
    as_of: AwareDatetime = Field(description="The agent's notion of now. Used instead of the system clock.")
    versions: Versions
    priority: Literal["urgent", "normal", "batch"] = "normal"
    language: Language = "en"
    budget: Budget
    previous_rejection: Optional[Rejection] = None

    @model_validator(mode="after")
    def _gate_off_only_in_simulation(self) -> "RunRequest":
        if self.execution_profile.gate == "off" and self.mode != "simulation":
            raise ValueError("gate off is only allowed in simulation")
        if self.mode == "live" and self.execution_profile.data_class == "synthetic":
            raise ValueError("live runs can't use the synthetic data class")
        return self


class CaseRef(Strict):
    case_id: Identifier
    sequence: Annotated[int, Field(ge=0, description="Company submission number within the case.")]
    case_type: Slug
    route: Optional[Slug] = None
    product_type: Optional[Slug] = None


class FactValue(Strict):
    """A value extracted from a document. Code compares values, never the model."""

    key: Slug
    status: Literal["found", "not_found", "ambiguous"]
    value: Optional[ShortText] = None
    number: Optional[float] = None
    unit: Optional[Annotated[str, Field(max_length=32)]] = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "FactValue":
        if self.status == "found" and (self.value is None or not self.evidence):
            raise ValueError("a found value needs a value and evidence")
        if self.status == "not_found" and (self.value is not None or self.number is not None):
            raise ValueError("a value that wasn't found can't have a value")
        return self


class ToolError(Strict):
    tool: Slug
    code: Slug
    message: ShortText
    retryable: bool


class ModelCall(Strict):
    model: Identifier
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(ge=0)]
    fallback: bool = False


class Usage(Strict):
    model_calls: Annotated[int, Field(ge=0)]
    tool_calls: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class AgentInfo(Strict):
    name: Slug
    version: Identifier
    prompt_hash: Fingerprint


RUN_ERROR_CODES = ("invalid_output", "output_truncated", "internal_error", "budget_exceeded")


class RunError(Strict):
    code: Literal["invalid_output", "output_truncated", "internal_error", "budget_exceeded"] = Field(
        description="`invalid_output`: the answer broke the schema, even after one retry. `output_truncated`: the model was cut off. "
        "`internal_error`: the agent failed for another reason. `budget_exceeded`: the run used its model or tool calls, or its deadline, before it could answer."
    )
    message: ShortText
    retryable: bool


RunStatus = Literal["queued", "running", "completed", "stopped_early", "failed", "cancelled"]
FinishReason = Literal["stop", "length", "budget", "deadline", "cancelled", "error"]
T = TypeVar("T", bound=BaseModel)


class RunResponse(Strict, Generic[T]):
    """Status of a run, with its result once finished."""

    run_id: RunId
    status: RunStatus
    finish_reason: Optional[FinishReason] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    agent: AgentInfo
    usage: Optional[Usage] = None
    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_errors: list[ToolError] = Field(
        default_factory=list, description="Tool failures during the run. A result with these may be incomplete."
    )
    error: Optional[RunError] = None
    result: Optional[T] = None

    @model_validator(mode="after")
    def _status_rules(self) -> "RunResponse":
        if self.status in ("queued", "running"):
            if self.result is not None or self.error is not None or self.finish_reason is not None:
                raise ValueError("an unfinished run has no result, error or finish reason")
        elif self.status == "completed":
            if self.result is None or self.error is not None or self.finish_reason != "stop":
                raise ValueError("a completed run needs a result, no error, and finish reason stop")
        elif self.status == "failed":
            if self.error is None or self.result is not None:
                raise ValueError("a failed run needs an error and has no result")
        elif self.status == "stopped_early":
            if self.finish_reason not in ("budget", "deadline"):
                raise ValueError("stopped_early needs finish reason budget or deadline")
        elif self.status == "cancelled" and self.finish_reason != "cancelled":
            raise ValueError("a cancelled run needs finish reason cancelled")
        return self


class Problem(Strict):
    """RFC 9457 problem details."""

    type: str = "about:blank"
    title: ShortText
    status: Annotated[int, Field(ge=400, le=599)]
    code: Slug
    detail: Optional[ShortText] = None
    run_id: Optional[str] = None


class Health(Strict):
    status: Literal["ok"]


def base_request(run_id: str, **overrides) -> dict:
    """Shared fields for contract examples. All example data is synthetic."""
    request = {
        "run_id": run_id,
        "run_token": "example-signed-token",
        "tenant": "example-country",
        "mode": "simulation",
        "execution_profile": {"model_set": "default", "gate": "on", "data_class": "synthetic"},
        "as_of": "2026-11-01T10:00:00Z",
        "versions": {"pack": "pack-3", "kb_snapshot": "snap-14"},
        "priority": "normal",
        "language": "en",
        "budget": {"max_model_calls": 12, "max_tool_calls": 20, "deadline_seconds": 180},
        "previous_rejection": None,
    }
    request.update(overrides)
    return request


def completed_response(run_id: str, agent: str, result: dict) -> dict:
    """A finished poll response, for contract examples."""
    return {
        "run_id": run_id,
        "status": "completed",
        "finish_reason": "stop",
        "started_at": "2026-11-01T10:00:02Z",
        "finished_at": "2026-11-01T10:00:41Z",
        "agent": {"name": agent, "version": "1.0.0", "prompt_hash": "sha256:7ab2"},
        "usage": {"model_calls": 4, "tool_calls": 7, "input_tokens": 18200, "output_tokens": 950},
        "model_calls": [
            {"model": "model-a", "input_tokens": 9000, "output_tokens": 400, "duration_ms": 8200, "fallback": False}
        ],
        "tool_errors": [],
        "error": None,
        "result": result,
    }
