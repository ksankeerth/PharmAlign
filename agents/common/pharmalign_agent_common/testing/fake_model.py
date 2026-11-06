"""A model that replays a script. No network, no keys.

    model = FakeModel(script=[
        call_tool("read_page", doc_id="d-17", page=2),
        say_json({"document_map": [], "facts": [], "issues": []}),
    ])

Each model call takes the next step. A step is a response, or a function that gets the
LlmRequest and returns one. Running out of steps fails the test.
"""
import asyncio
import json
from collections.abc import Callable
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel, PrivateAttr

Step = LlmResponse | Callable[[LlmRequest], LlmResponse]


def _response(parts: list[types.Part], finish: types.FinishReason, tokens: tuple[int, int]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=parts),
        finish_reason=finish,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens[0], candidates_token_count=tokens[1], total_token_count=sum(tokens)
        ),
    )


def call_tool(name: str, tokens: tuple[int, int] = (100, 10), **args: Any) -> LlmResponse:
    """The model asks for one tool call."""
    return _response([types.Part(function_call=types.FunctionCall(name=name, args=args))], types.FinishReason.STOP, tokens)


def say(text: str, tokens: tuple[int, int] = (100, 20)) -> LlmResponse:
    """The model answers with text."""
    return _response([types.Part(text=text)], types.FinishReason.STOP, tokens)


def say_json(value: BaseModel | dict | list, tokens: tuple[int, int] = (100, 20)) -> LlmResponse:
    """The model answers with JSON."""
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return say(json.dumps(data), tokens)


def cut_off(text: str = '{"document_map": [', tokens: tuple[int, int] = (100, 1000)) -> LlmResponse:
    """The model runs out of output tokens (finish reason `length`)."""
    return _response([types.Part(text=text)], types.FinishReason.MAX_TOKENS, tokens)


class FakeModel(BaseLlm):
    model: str = "fake-model"
    script: list[Any] = []
    delay_seconds: float = 0.0
    _next: int = PrivateAttr(default=0)
    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> list[LlmRequest]:
        """Every request the model received, in order."""
        return self._requests

    @property
    def calls(self) -> int:
        return self._next

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self._requests.append(llm_request)
        if self._next >= len(self.script):
            raise AssertionError(f"FakeModel script ran out after {self._next} calls")
        step = self.script[self._next]
        self._next += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        yield step(llm_request) if callable(step) else step
