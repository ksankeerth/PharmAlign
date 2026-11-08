"""ADK glue: builds the agent for one run, runs it, and controls what comes out.

Rules this file keeps:
- Tools come only from MCP, filtered to the contract's tool list. The agent has no data access of its own.
- The run token goes out as a bearer header, and a new toolset (and connection) is built for every run.
- The final answer must validate against the contract's result model and the agent's rules, which see
  what the run's tools returned. One retry with the reason, then fail.
- Budgets in the request are enforced: model calls and tool calls here, time by the caller.
"""
import json
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from google.genai import types
from pharmalign_contracts.common import ModelCall, ToolError, Usage
from pydantic import ValidationError

from .errors import BudgetExceeded, OutputInvalid, OutputTruncated
from .evidence import ToolLog
from . import scripted
from .settings import Settings
from .spec import AgentSpec

_SLUG = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass
class RunOutcome:
    result: Any
    usage: Usage | None = None
    model_calls: list[ModelCall] = field(default_factory=list)
    tool_errors: list[ToolError] = field(default_factory=list)


@dataclass
class RunTracker:
    """Counts what one run spends, and enforces the budget."""

    max_model_calls: int
    max_tool_calls: int
    model_calls: list[ModelCall] = field(default_factory=list)
    tool_errors: list[ToolError] = field(default_factory=list)
    tool_calls: int = 0
    started: int = 0  # model calls started
    truncated: bool = False
    log: ToolLog = field(default_factory=ToolLog)  # what the tools returned, for the result rules
    exceeded: str | None = None  # set when a budget is hit; ADK may wrap the exception
    _t0: float = 0.0

    def before_model(self) -> None:
        if self.started >= self.max_model_calls:
            self.exceeded = f"more than {self.max_model_calls} model calls"
            raise BudgetExceeded(self.exceeded)
        self.started += 1
        self._t0 = time.monotonic()

    def after_model(self, model: str, response) -> None:
        usage = response.usage_metadata
        self.model_calls.append(
            ModelCall(
                model=model or "unknown",
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
                duration_ms=int((time.monotonic() - self._t0) * 1000),
            )
        )
        if response.finish_reason == types.FinishReason.MAX_TOKENS:
            self.truncated = True

    def before_tool(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            self.exceeded = f"more than {self.max_tool_calls} tool calls"
            raise BudgetExceeded(self.exceeded)
        self.tool_calls += 1

    def tool_failed(self, tool: str, message: str) -> None:
        name = tool if _SLUG.fullmatch(tool) else "unknown"
        self.tool_errors.append(ToolError(tool=name, code="tool_error", message=(message or "failed")[:500], retryable=True))

    def usage(self) -> Usage:
        return Usage(
            model_calls=len(self.model_calls),
            tool_calls=self.tool_calls,
            input_tokens=sum(c.input_tokens for c in self.model_calls),
            output_tokens=sum(c.output_tokens for c in self.model_calls),
        )


def build_model(spec: AgentSpec, settings: Settings) -> BaseLlm:
    if settings.model_mode == "scripted":
        return scripted.unused_model()  # a run or session brings its own scripted model
    settings.apply_model_mode()
    from .vertex_schema import VertexSchemaGemini  # Vertex rejects union result schemas as written; the API-key path is left as it was

    return (VertexSchemaGemini if settings.model_mode == "vertex" else Gemini)(
        model=settings.model_for(spec.name, spec.default_model),
        retry_options=types.HttpRetryOptions(attempts=3),
    )


def mcp_toolset(spec: AgentSpec, settings: Settings, run_token: str | None) -> McpToolset:
    """A toolset for one run. The token is fixed here, so it can't reach another run."""
    return toolset_for(spec.tools, settings, run_token)


def toolset_for(tools: Sequence[str], settings: Settings, run_token: str | None) -> McpToolset:
    """A toolset limited to these tools, with its own connection and the token fixed."""
    headers = {"Authorization": f"Bearer {run_token}"} if run_token else {}

    def header_provider(_: ReadonlyContext) -> dict[str, str]:
        return dict(headers)

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=settings.mcp_url, timeout=settings.mcp_timeout_seconds
        ),
        tool_filter=list(tools),
        header_provider=header_provider,
    )


def build_agent(
    spec: AgentSpec,
    settings: Settings,
    *,
    run_token: str | None = None,
    model: BaseLlm | None = None,
    tracker: RunTracker | None = None,
    instruction: str | None = None,
) -> LlmAgent:
    """The ADK agent: model setting, instruction, output schema, MCP tools."""
    model = model or build_model(spec, settings)
    text = instruction or spec.instruction
    callbacks: dict[str, Any] = {}
    if tracker is not None:
        callbacks = _callbacks(tracker, getattr(model, "model", ""))
    return LlmAgent(
        name=spec.adk_name,
        model=model,
        instruction=lambda _: text,  # a function, so ADK doesn't template the text
        output_schema=spec.result_model,
        tools=[mcp_toolset(spec, settings, run_token)] if settings.mcp_url else [],
        **callbacks,
    )


# A tool's answer, not a failure: the run goes on and is complete. invalid_arguments is the model's own mistake (say a
# one-letter search): the tool tells it why, it asks again, and every claim it makes still needs a quote a tool returned.
ANSWER_CODES = frozenset({"not_found", "outside_check_sources", "invalid_arguments"})


def _error_code(tool_response: dict) -> Optional[str]:
    """The code in a tool's JSON error body, such as not_found. None when the body is not one."""
    try:
        body = json.loads(tool_response["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return body.get("code") if isinstance(body, dict) else None


def _callbacks(tracker: RunTracker, model_name: str) -> dict[str, Any]:
    def before_model(callback_context, llm_request):
        tracker.before_model()

    def after_model(callback_context, llm_response):
        tracker.after_model(model_name, llm_response)

    def before_tool(tool, args, tool_context):
        if tool.name != "set_model_response":  # ADK's own tool for the final structured answer
            tracker.before_tool()

    def after_tool(tool, args, tool_context, tool_response):
        if tool.name == "set_model_response":
            return
        if isinstance(tool_response, dict) and tool_response.get("isError") and _error_code(tool_response) not in ANSWER_CODES:
            tracker.tool_failed(tool.name, json.dumps(tool_response.get("content", ""))[:500])
        tracker.log.record(tool.name, args, tool_response)

    def on_tool_error(tool, args, tool_context, error):
        tracker.tool_failed(tool.name, f"{type(error).__name__}: {error}")
        return {"error": f"The tool {tool.name} failed. Do not guess its result."}

    return {
        "before_model_callback": before_model,
        "after_model_callback": after_model,
        "before_tool_callback": before_tool,
        "after_tool_callback": after_tool,
        "on_tool_error_callback": on_tool_error,
    }


def _final_text(event) -> str:
    parts = event.content.parts if event.content and event.content.parts else []
    return "".join(p.text for p in parts if p.text and not p.thought)


def parse_output(spec: AgentSpec, request: Any, text: str, log: ToolLog | None = None):
    """Validates the final answer against the result model and the agent's rules."""
    text = text.strip()
    if fenced := _FENCE.match(text):
        text = fenced.group(1)
    try:
        result = spec.result_model.model_validate_json(text)
        result = spec.apply_rules(request, result, log or ToolLog())
    except (ValidationError, ValueError) as exc:
        raise OutputInvalid(str(exc)) from exc
    return result


async def _ask(runner: Runner, session_id: str, message: str, tracker: RunTracker) -> str:
    """Sends one user message and returns the agent's final text."""
    final = ""
    content = types.Content(role="user", parts=[types.Part(text=message)])
    async for event in runner.run_async(user_id="server", session_id=session_id, new_message=content):
        if tracker.exceeded:
            raise BudgetExceeded(tracker.exceeded)
        if tracker.truncated:
            raise OutputTruncated("the model hit its output limit")
        if event.error_code:
            raise RuntimeError(f"model error {event.error_code}: {event.error_message}")
        if event.is_final_response():
            final = _final_text(event)
    if tracker.exceeded:
        raise BudgetExceeded(tracker.exceeded)
    if tracker.truncated:
        raise OutputTruncated("the model hit its output limit")
    return final


async def run_agent(
    spec: AgentSpec,
    request: Any,
    *,
    settings: Settings,
    model: BaseLlm | None = None,
) -> RunOutcome:
    """Runs one contract run. Raises BudgetExceeded, OutputInvalid or OutputTruncated, never a half result."""
    if not settings.mcp_url:
        raise RuntimeError("PHARMALIGN_MCP_URL is not set")
    if reason := scripted.refuse_live(settings.model_mode, request.mode):
        raise scripted.ScriptedInLive(reason)
    if model is None and settings.model_mode == "scripted":
        model = scripted.model_for_run(spec.name, request)
    tracker = RunTracker(request.budget.max_model_calls, request.budget.max_tool_calls)
    agent = build_agent(
        spec, settings, run_token=request.run_token, model=model, tracker=tracker,
        instruction=spec.instruction_for(request),
    )
    sessions = InMemorySessionService()
    runner = Runner(app_name=spec.adk_name, agent=agent, session_service=sessions, auto_create_session=True)
    session_id = f"{request.run_id}-{uuid.uuid4().hex[:8]}"
    try:
        text = await _ask(runner, session_id, spec.build_message(request), tracker)
        try:
            result = parse_output(spec, request, text, tracker.log)
        except OutputInvalid as first:
            retry = (
                f"Your answer was rejected: {first}\n"
                "Reply again with only the corrected JSON for the result schema."
            )
            text = await _ask(runner, session_id, retry, tracker)
            result = parse_output(spec, request, text, tracker.log)  # a second failure ends the run
    finally:
        await runner.close()
    return RunOutcome(
        result=result, usage=tracker.usage(), model_calls=tracker.model_calls, tool_errors=tracker.tool_errors
    )
