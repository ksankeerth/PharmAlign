import asyncio

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import errors, types
from pharmalign_agent_common.testing import FakeModel
from pharmalign_agent_common.testing.fake_model import say, say_json
from pydantic import BaseModel

from app.chat import models
from app.chat.models import AllModelsBusy, ModelCall, ModelChain, ModelFailed, chain_from_env, reset_cooldowns


class Out(BaseModel):
    ok: bool


def busy(code: int = 429):
    def step(request: LlmRequest):
        raise errors.APIError(code, {"error": {"code": code, "message": "busy", "status": "RESOURCE_EXHAUSTED"}})

    return step


def bad_request(request: LlmRequest):
    raise errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _cooldowns():
    reset_cooldowns()
    yield
    reset_cooldowns()


def make(first: list, second: list, clock=None, **kw):
    kw.setdefault("retry_seconds", 0)
    fakes = {"m1": FakeModel(model="m1", script=first), "m2": FakeModel(model="m2", script=second)}
    chain = ModelChain("classifier", ["m1", "m2"], llm_factory=lambda name: fakes[name], clock=clock or Clock(), **kw)
    return chain, fakes["m1"], fakes["m2"]


async def test_first_model_answers():
    chain, m1, m2 = make([say_json({"ok": True}, tokens=(120, 15))], [])
    result = await chain.generate(system="sys", prompt="hello", schema=Out)
    assert result.model == "m1" and result.text == '{"ok": true}'
    assert m2.calls == 0
    call = result.calls[-1]
    assert (call.input_tokens, call.output_tokens, call.thought_tokens, call.fallback_from, call.error) == (120, 15, 0, None, None)


async def test_prompt_and_system_are_separate_and_json_is_asked_for_with_a_schema():
    chain, m1, _ = make([say("x")], [])
    await chain.generate(system="the rules", prompt="the data", schema=Out)
    request = m1.requests[0]
    assert request.config.system_instruction == "the rules"
    assert request.contents[0].parts[0].text == "the data"
    assert request.config.response_mime_type == "application/json"
    assert request.config.response_schema is Out


async def test_no_schema_means_no_json_mode():
    chain, m1, _ = make([say("x")], [])
    await chain.generate(system="s", prompt="p")
    assert m1.requests[0].config.response_mime_type is None


async def test_thought_tokens_are_captured_and_thought_text_is_dropped():
    response = say("answer")
    response.content.parts.insert(0, types.Part(text="thinking...", thought=True))
    response.usage_metadata.thoughts_token_count = 40
    chain, _, _ = make([response], [])
    result = await chain.generate(system="s", prompt="p")
    assert result.text == "answer"
    assert result.calls[-1].thought_tokens == 40


async def test_falls_back_on_429_and_cools_the_model_down_for_60_seconds():
    clock = Clock()
    chain, m1, m2 = make([busy(429), busy(429)], [say("a"), say("b"), say("c")], clock)
    first = await chain.generate(system="s", prompt="p")
    assert first.model == "m2"
    assert [c.model for c in first.calls] == ["m1", "m1", "m2"]
    assert first.calls[0].error and first.calls[0].fallback_from is None
    assert first.calls[1].error and first.calls[1].fallback_from is None
    assert first.calls[2].fallback_from == "m1" and first.calls[2].error is None

    clock.now += 59
    second = await chain.generate(system="s", prompt="p")
    assert second.model == "m2" and m1.calls == 2
    assert [c.model for c in second.calls] == ["m2"] and second.calls[0].fallback_from == "m1"

    clock.now += 2
    m1.script.append(say("back"))
    third = await chain.generate(system="s", prompt="p")
    assert third.model == "m1" and third.text == "back"


@pytest.mark.parametrize("code", [500, 502, 503, 504])
async def test_5xx_is_busy(code):
    chain, _, m2 = make([busy(code), busy(code)], [say("a")])
    assert (await chain.generate(system="s", prompt="p")).model == "m2"


async def test_a_busy_model_is_tried_once_more_before_falling_back():
    chain, m1, m2 = make([busy(429), say("again")], [])
    result = await chain.generate(system="s", prompt="p")
    assert result.model == "m1" and result.text == "again" and m2.calls == 0
    assert [c.model for c in result.calls] == ["m1", "m1"]
    assert result.calls[0].error and result.calls[1].error is None and result.calls[1].fallback_from is None


async def test_two_busy_answers_cool_the_model_down_and_fall_back_after_three_calls():
    chain, m1, m2 = make([busy(429), busy(429)], [say("fine"), say("again")])
    result = await chain.generate(system="s", prompt="p")
    assert result.model == "m2" and m1.calls == 2
    assert [c.model for c in result.calls] == ["m1", "m1", "m2"]
    assert all(c.error for c in result.calls[:2]) and result.calls[2].fallback_from == "m1"
    assert (await chain.generate(system="s", prompt="p")).calls[0].model == "m2"  # cooling down


async def test_a_timeout_falls_back_at_once_without_a_same_model_retry():
    chain, m1, m2 = make([say("slow"), say("slow")], [say("quick")])
    m1.delay_seconds = 0.5
    result = await chain.generate(system="s", prompt="p", timeout=0.05)
    assert [c.model for c in result.calls] == ["m1", "m2"] and m1.calls == 1


async def test_the_retry_waits_retry_seconds(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(models.asyncio, "sleep", fake_sleep)
    chain, _, _ = make([busy(), say("ok")], [], retry_seconds=2.0)
    await chain.generate(system="s", prompt="p")
    assert waits == [2.0]


async def test_a_400_gets_no_retry():
    chain, m1, _ = make([bad_request, say("fine")], [])
    with pytest.raises(ModelFailed) as info:
        await chain.generate(system="s", prompt="p")
    assert m1.calls == 1 and len(info.value.calls) == 1


async def test_cooldown_is_shared_between_chains():
    clock = Clock()
    chain, m1, _ = make([busy(), busy()], [say("a")], clock)
    await chain.generate(system="s", prompt="p")
    other = ModelChain("answer", ["m1", "m2"], llm_factory=lambda name: FakeModel(model=name, script=[say("z")]), clock=clock)
    assert (await other.generate(system="s", prompt="p")).model == "m2"


async def test_400_is_raised_without_fallback_or_cooldown():
    clock = Clock()
    chain, m1, m2 = make([bad_request, say("fine")], [], clock)
    with pytest.raises(ModelFailed) as info:
        await chain.generate(system="s", prompt="p")
    assert isinstance(info.value.__cause__, errors.ClientError)
    assert m2.calls == 0
    assert info.value.calls[0].error.startswith("ClientError")
    assert (await chain.generate(system="s", prompt="p")).model == "m1"


async def test_all_busy_raises_with_the_calls():
    chain, _, _ = make([busy(), busy()], [busy(), busy()])
    with pytest.raises(AllModelsBusy) as info:
        await chain.generate(system="s", prompt="p")
    assert [c.model for c in info.value.calls] == ["m1", "m1", "m2", "m2"]
    with pytest.raises(AllModelsBusy) as again:
        await chain.generate(system="s", prompt="p")
    assert again.value.calls == []


async def test_timeout_counts_as_busy_when_set():
    chain, m1, m2 = make([say("slow")], [say("quick")])
    m1.delay_seconds = 0.5
    result = await chain.generate(system="s", prompt="p", timeout=0.05)
    assert result.model == "m2"
    assert "Timeout" in result.calls[0].error


async def test_cancelled_call_is_recorded_in_the_callers_list():
    chain, m1, _ = make([say("slow")], [])
    m1.delay_seconds = 0.5
    calls: list[ModelCall] = []
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await chain.generate(system="s", prompt="p", calls=calls)
    assert [c.model for c in calls] == ["m1"] and calls[0].error == "cancelled"


async def test_one_llm_per_model_per_chain():
    made: list[str] = []

    def factory(name: str):
        made.append(name)
        return FakeModel(model=name, script=[say("a"), say("b")])

    chain = ModelChain("answer", ["m1"], llm_factory=factory, clock=Clock())
    await chain.generate(system="s", prompt="p")
    await chain.generate(system="s", prompt="p")
    assert made == ["m1"]


async def test_thinking_level_per_model_and_no_sampling_settings():
    fakes = {n: FakeModel(model=n, script=[busy(), busy()] if n == "gemini-3.5-flash-lite" else [say("a")])
             for n in ("gemini-3.5-flash-lite", "gemini-3.8-flash")}
    chain = ModelChain("answer", ["gemini-3.5-flash-lite", "gemini-3.8-flash"], llm_factory=lambda n: fakes[n], clock=Clock(), retry_seconds=0)
    await chain.generate(system="s", prompt="p")
    lite = fakes["gemini-3.5-flash-lite"].requests[0].config
    full = fakes["gemini-3.8-flash"].requests[0].config
    assert lite.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert full.thinking_config.thinking_level == types.ThinkingLevel.LOW
    for config in (lite, full):
        assert config.temperature is None and config.top_p is None and config.top_k is None


async def test_thinking_override_from_env():
    fake = FakeModel(model="gemini-3.8-flash", script=[say("a")])
    chain = chain_from_env(
        "answer",
        {"PHARMALIGN_MODEL_KA_ANSWER": "gemini-3.8-flash", "PHARMALIGN_KA_THINKING": "gemini-3.8-flash=medium"},
        llm_factory=lambda n: fake,
    )
    await chain.generate(system="s", prompt="p")
    assert fake.requests[0].config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM


def test_chain_from_env_defaults_and_overrides():
    assert chain_from_env("classifier", {}, llm_factory=lambda n: None).models == ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    assert chain_from_env("answer", {}, llm_factory=lambda n: None).models == ("gemini-3.8-flash", "gemini-3.5-flash-lite")
    custom = chain_from_env("answer", {"PHARMALIGN_MODEL_KA_ANSWER": " a , b "}, llm_factory=lambda n: None)
    assert custom.models == ("a", "b")


def test_as_log_keys():
    log = ModelCall(role="classifier", model="m1", input_tokens=1, output_tokens=2, thought_tokens=3, ms=4).as_log()
    assert log == {"role": "classifier", "model": "m1", "in": 1, "out": 2, "thought": 3, "ms": 4, "fallback": None, "error": None}


@pytest.mark.parametrize("env", [{"GEMINI_API_KEY": "k"}, {"GOOGLE_API_KEY": "k"}, {"PHARMALIGN_MODEL_MODE": "scripted"}])
def test_default_factory_refuses_api_key_and_scripted(monkeypatch, env):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "PHARMALIGN_MODEL_MODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        models.default_llm_factory("gemini-3.8-flash")


def test_the_classifier_chain_falls_back_at_once_and_the_answer_chain_retries_first():
    from app.chat.models import chain_from_env
    assert chain_from_env("classifier", env={}, llm_factory=lambda name: None).retry_seconds is None
    assert chain_from_env("answer", env={}, llm_factory=lambda name: None).retry_seconds == 2.0


def test_cooldown_seconds_comes_from_the_env_and_falls_back_to_60():
    def seconds(env):
        return chain_from_env("answer", env, llm_factory=lambda n: None).cooldown_seconds

    assert seconds({}) == 60.0
    assert seconds({"PHARMALIGN_KA_COOLDOWN_SECONDS": "10"}) == 10.0
    assert seconds({"PHARMALIGN_KA_COOLDOWN_SECONDS": "2.5"}) == 2.5
    assert seconds({"PHARMALIGN_KA_COOLDOWN_SECONDS": "0"}) == 0.0
    for bad in ("abc", "-5", "nan", "inf", ""):
        assert seconds({"PHARMALIGN_KA_COOLDOWN_SECONDS": bad}) == 60.0
