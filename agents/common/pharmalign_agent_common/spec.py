"""What makes one agent different from another: its contract types, instruction and rules.

`check_result(request, result)` raises ValueError to reject a result. A rule that also takes a
third argument gets the run's ToolLog, and may return a replacement result (or None to keep it).

`variants` are extra instruction texts, one picked per run by `pick_variant(request)` and added
after `instruction`. The prompt hash covers all of them.

`schema_for(request)` may give the model a narrower result schema for one run, such as a map's keys written out as fields.
Gemini's structured output can't fill a map with open keys (it returns `{}`), so a run that knows the keys names them. The
answer is still parsed and checked as `result_model`.
"""
import hashlib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pharmalign_contracts.agents.run_agents import RUN_AGENTS
from pydantic import BaseModel

from .evidence import ToolLog


def _no_rules(request: Any, result: Any) -> None:
    return None


@dataclass(frozen=True)
class AgentSpec:
    name: str  # the contract name, such as "dossier-reader"
    adk_name: str  # the ADK agent name, such as "dossier_reader"
    path: str  # contract route, such as "/dossier-reads"
    request_model: type[BaseModel]
    run_model: type[BaseModel]
    result_model: type[BaseModel]
    tools: tuple[str, ...]  # the only MCP tools the agent may use
    instruction: str
    default_model: str
    build_message: Callable[[Any], str]  # request -> first user message
    check_result: Callable[..., Any] = _no_rules  # (request, result[, log]); raise ValueError to reject
    variants: Mapping[str, str] = field(default_factory=dict)
    pick_variant: Callable[[Any], str] | None = None  # request -> variant name
    schema_for: Callable[[Any], type[BaseModel]] | None = None  # request -> the model's result schema for this run
    # When set, a tool call the same as an earlier one in the run (same tool, same arguments) is not sent again: the model gets
    # this text instead. For an agent that re-reads tools in a loop. None: every call is sent, as before.
    repeat_answer: str | None = None
    # The agent's own ceiling on model calls, below the request's budget when that is higher. None: the request's budget only.
    max_model_calls: int | None = None
    # When True, the model is made to answer: after a repeated call (with repeat_answer set), and on the last model call the
    # budget allows, the next model call is sent with function calling off. For a model that keeps asking for tools instead of
    # answering. False: every model call may call tools, as before.
    force_answer: bool = False

    def output_schema_for(self, request: Any) -> type[BaseModel]:
        return self.schema_for(request) if self.schema_for else self.result_model

    @property
    def prompt_hash(self) -> str:
        text = self.instruction + "".join(f"\n\n[{k}]\n{v}" for k, v in sorted(self.variants.items()))
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

    def instruction_for(self, request: Any) -> str:
        if self.pick_variant is None:
            return self.instruction
        return self.instruction + "\n\n" + self.variants[self.pick_variant(request)]

    def apply_rules(self, request: Any, result: Any, log: ToolLog) -> Any:
        """Runs the agent's rules. Returns the result to keep."""
        if len(inspect.signature(self.check_result).parameters) >= 3:
            replaced = self.check_result(request, result, log)
            return result if replaced is None else replaced
        self.check_result(request, result)
        return result

    def version(self) -> str:
        try:
            return version(self.name)
        except PackageNotFoundError:
            return "0.0.0"


def agent_spec(
    name: str,
    *,
    adk_name: str,
    result_model: type[BaseModel],
    instruction: str,
    default_model: str,
    build_message: Callable[[Any], str],
    check_result: Callable[..., Any] = _no_rules,
    variants: Mapping[str, str] | None = None,
    pick_variant: Callable[[Any], str] | None = None,
    schema_for: Callable[[Any], type[BaseModel]] | None = None,
    repeat_answer: str | None = None,
    max_model_calls: int | None = None,
    force_answer: bool = False,
) -> AgentSpec:
    """Builds a spec, taking path, request, run model and tools from the shared contracts."""
    entry = next(a for a in RUN_AGENTS if a.name == name)
    return AgentSpec(
        name=name,
        adk_name=adk_name,
        path=entry.path,
        request_model=entry.request,
        run_model=entry.run,
        result_model=result_model,
        tools=entry.tools,
        instruction=instruction,
        default_model=default_model,
        build_message=build_message,
        check_result=check_result,
        variants=dict(variants or {}),
        pick_variant=pick_variant,
        schema_for=schema_for,
        repeat_answer=repeat_answer,
        max_model_calls=max_model_calls,
        force_answer=force_answer,
    )
