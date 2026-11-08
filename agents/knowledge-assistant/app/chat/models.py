"""Model chains for the chat pipeline: ordered models, a shared cooldown, one thin call that records usage.

Rules:
- Calls go through ADK's BaseLlm interface, so tests can use FakeModel.
- Never set temperature, top_p or top_k (deprecated for Gemini 3). Only the thinking level is set.
- A model that answers 429 or 5xx is tried once more after `retry_seconds` (default 2). If that fails as busy too,
  or the call runs out of time, the model is skipped for 60 seconds by every chain in the process, and the next one
  is used. A timeout gets no same-model retry. Any other error is raised at once and does not cool the model down.
- The pipeline runs on Vertex AI with Application Default Credentials. An API key or scripted mode is refused.

PHARMALIGN_MODEL_KA_CLASSIFIER   comma list, first is preferred (default gemini-3.5-flash-lite,gemini-3.1-flash-lite)
PHARMALIGN_MODEL_KA_ANSWER       comma list, first is preferred (default gemini-3.8-flash,gemini-3.5-flash-lite)
A call can carry a `deadline` (a time.monotonic() value): no attempt starts with under 1 s left, an attempt gets at
most the time left, and a timeout caused by that cut is not the model's fault, so it does not start a cooldown.

PHARMALIGN_KA_THINKING           optional "model=level,model=level". Default: "minimal" for flash-lite models, else "low"
"""
import asyncio
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from google.adk.models import Gemini
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import errors, types
from pharmalign_agent_common import Settings
from pydantic import BaseModel

Role = Literal["classifier", "answer"]
LlmFactory = Callable[[str], BaseLlm]

DEFAULTS: dict[str, str] = {
    "classifier": "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
    "answer": "gemini-3.8-flash,gemini-3.5-flash-lite",
}
ENV_NAMES: dict[str, str] = {"classifier": "PHARMALIGN_MODEL_KA_CLASSIFIER", "answer": "PHARMALIGN_MODEL_KA_ANSWER"}
THINKING_ENV = "PHARMALIGN_KA_THINKING"
COOLDOWN_SECONDS = 60.0
COOLDOWN_ENV = "PHARMALIGN_KA_COOLDOWN_SECONDS"
RETRY_SECONDS = 2.0
MIN_ATTEMPT_SECONDS = 1.0  # an attempt that has less than this before the deadline is not started

_COOLDOWNS: dict[str, float] = {}  # model name -> time it may be used again, shared by every chain in the process


def env_seconds(name: str, default: float) -> float:
    """A positive, finite float from the environment; missing or invalid means the default."""
    try:
        value = float(os.environ.get(name) or default)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def reset_cooldowns() -> None:
    _COOLDOWNS.clear()


@dataclass
class ModelCall:
    """One attempt at one model. `error` is set on a failed attempt."""

    role: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    ms: int = 0
    fallback_from: str | None = None
    error: str | None = None

    def as_log(self) -> dict:
        return {
            "role": self.role,
            "model": self.model,
            "in": self.input_tokens,
            "out": self.output_tokens,
            "thought": self.thought_tokens,
            "ms": self.ms,
            "fallback": self.fallback_from,
            "error": self.error,
        }


@dataclass
class ModelResult:
    text: str  # the joined text parts, thoughts left out
    model: str
    calls: list[ModelCall] = field(default_factory=list)  # every attempt, the last one is the success


class AllModelsBusy(Exception):
    """Every model in the chain is cooling down or just failed as busy."""

    def __init__(self, role: str, calls: list[ModelCall]):
        super().__init__(f"all {role} models are busy")
        self.calls = calls


class ModelFailed(Exception):
    """A call failed in a way that is not 'busy' (for example a 400). The original error is `__cause__`."""

    def __init__(self, model: str, calls: list[ModelCall]):
        super().__init__(f"{model} failed: {calls[-1].error if calls else 'unknown'}")
        self.calls = calls


def default_llm_factory(name: str) -> BaseLlm:
    settings = Settings.from_env()
    if settings.model_mode == "scripted":
        raise RuntimeError("The chat pipeline has no scripted mode. Unset PHARMALIGN_MODEL_MODE=scripted.")
    if settings.model_mode == "api_key":
        raise RuntimeError(
            "The chat pipeline uses Vertex AI with Application Default Credentials. "
            "Remove GEMINI_API_KEY / GOOGLE_API_KEY (and PHARMALIGN_MODEL_MODE=api_key) from the environment."
        )
    settings.apply_model_mode()
    return Gemini(model=name, retry_options=types.HttpRetryOptions(attempts=1))


def default_thinking_level(model: str) -> str:
    return "minimal" if "flash-lite" in model else "low"


def parse_thinking(raw: str | None) -> dict[str, str]:
    """"gemini-3.8-flash=low,gemini-3.5-flash-lite=minimal" to a dict."""
    levels: dict[str, str] = {}
    for item in (raw or "").split(","):
        name, _, level = item.partition("=")
        if name.strip() and level.strip():
            levels[name.strip()] = level.strip().lower()
    return levels


def is_busy(error: BaseException, timeout_set: bool) -> bool:
    if isinstance(error, errors.APIError):
        return error.code == 429 or (isinstance(error.code, int) and error.code >= 500)
    return timeout_set and isinstance(error, asyncio.TimeoutError)


class ModelChain:
    def __init__(
        self,
        role: str,
        models: Sequence[str],
        llm_factory: LlmFactory = default_llm_factory,
        cooldown_seconds: float = COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        thinking: Mapping[str, str] | None = None,
        retry_seconds: float | None = RETRY_SECONDS,  # None: no same-model retry
    ):
        if not models:
            raise ValueError(f"no models for {role}")
        self.role = role
        self.models = tuple(models)
        self.llm_factory = llm_factory
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.retry_seconds = retry_seconds
        self.thinking = dict(thinking or {})
        self._llms: dict[str, BaseLlm] = {}

    def thinking_level(self, model: str) -> str:
        return self.thinking.get(model) or default_thinking_level(model)

    def _llm(self, model: str) -> BaseLlm:
        if model not in self._llms:
            self._llms[model] = self.llm_factory(model)
        return self._llms[model]

    def _request(self, model: str, system: str, prompt: str, schema: type[BaseModel] | None) -> LlmRequest:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(self.thinking_level(model).upper())),
        )
        return LlmRequest(model=model, contents=[types.Content(role="user", parts=[types.Part(text=prompt)])], config=config)

    async def _call(self, llm: BaseLlm, request: LlmRequest):
        last = None
        async for response in llm.generate_content_async(request, stream=False):
            last = response
        if last is None:
            raise RuntimeError("the model returned no response")
        if getattr(last, "error_code", None) and not (last.content and last.content.parts):
            raise RuntimeError(f"model error {last.error_code}: {last.error_message}")
        return last

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        calls: list[ModelCall] | None = None,
        deadline: float | None = None,
    ) -> ModelResult:
        """Try each model in order. Pass `calls` to keep a live record of attempts even if the caller cancels us.

        `deadline` is a time.monotonic() value: the whole call must end by then (AllModelsBusy when it cannot)."""
        calls = [] if calls is None else calls
        skipped: str | None = None
        for model in self.models:
            if _COOLDOWNS.get(model, 0.0) > self.clock():
                skipped = skipped or model
                continue
            fallback_from = skipped
            response = None
            cut_by_deadline = False  # the last attempt timed out only because the deadline shortened it
            for attempt in range(2):
                attempt_timeout = timeout
                cut_by_deadline = False
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left < MIN_ATTEMPT_SECONDS:
                        raise AllModelsBusy(self.role, calls)
                    if timeout is None or left < timeout:
                        attempt_timeout, cut_by_deadline = left, True
                call = ModelCall(role=self.role, model=model, fallback_from=fallback_from)
                calls.append(call)
                started = time.monotonic()
                try:
                    llm = self._llm(model)
                    request = self._request(model, system, prompt, schema)
                    if attempt_timeout is not None:
                        response = await asyncio.wait_for(self._call(llm, request), attempt_timeout)
                    else:
                        response = await self._call(llm, request)
                except asyncio.CancelledError:
                    call.ms = round((time.monotonic() - started) * 1000)
                    call.error = "cancelled"
                    raise
                except Exception as error:
                    call.ms = round((time.monotonic() - started) * 1000)
                    call.error = f"{type(error).__name__}: {error}"[:300]
                    if not is_busy(error, attempt_timeout is not None):
                        raise ModelFailed(model, calls) from error
                    cut_by_deadline = cut_by_deadline and isinstance(error, asyncio.TimeoutError)
                    retry_fits = deadline is None or deadline - time.monotonic() - (self.retry_seconds or 0) >= MIN_ATTEMPT_SECONDS
                    if attempt == 0 and self.retry_seconds is not None and isinstance(error, errors.APIError) and retry_fits:  # busy: one more go at the same model
                        fallback_from = None
                        await asyncio.sleep(self.retry_seconds)
                        continue
                    break
                break
            if response is None:
                if not cut_by_deadline:  # a timeout the deadline caused is not the model's fault
                    _COOLDOWNS[model] = self.clock() + self.cooldown_seconds
                skipped = model
                continue
            call.ms = round((time.monotonic() - started) * 1000)
            usage = response.usage_metadata
            if usage:
                call.input_tokens = usage.prompt_token_count or 0
                call.output_tokens = usage.candidates_token_count or 0
                call.thought_tokens = usage.thoughts_token_count or 0
            parts = response.content.parts if response.content and response.content.parts else []
            text = "".join(p.text for p in parts if p.text and not p.thought)
            return ModelResult(text=text, model=model, calls=calls)
        raise AllModelsBusy(self.role, calls)


def cooldown_from_env(env: Mapping[str, str]) -> float:
    """PHARMALIGN_KA_COOLDOWN_SECONDS as a float; missing, invalid, negative or not finite means the default (60)."""
    try:
        value = float(env.get(COOLDOWN_ENV) or COOLDOWN_SECONDS)
    except ValueError:
        return COOLDOWN_SECONDS
    return value if math.isfinite(value) and value >= 0 else COOLDOWN_SECONDS


def chain_from_env(
    role: Role, env: Mapping[str, str] | None = None, llm_factory: LlmFactory | None = None
) -> ModelChain:
    env = os.environ if env is None else env
    raw = env.get(ENV_NAMES[role]) or DEFAULTS[role]
    models = [m.strip() for m in raw.split(",") if m.strip()]
    # The classifier has 3 s in all, so it falls back at once instead of waiting to retry the same model.
    retry = None if role == "classifier" else RETRY_SECONDS
    return ModelChain(role, models, llm_factory or default_llm_factory, thinking=parse_thinking(env.get(THINKING_ENV)),
                      retry_seconds=retry, cooldown_seconds=cooldown_from_env(env))
