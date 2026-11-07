"""Result schemas for Vertex: a union of event types must pass the client's own conversion, and say the same thing."""
import asyncio
from typing import Annotated, Literal, Union

import pytest
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.genai import Client, types
from google.genai import _transformers as transformers
from pydantic import BaseModel, ConfigDict, Field

from pharmalign_agent_common.runner import build_model
from pharmalign_agent_common.settings import Settings
from pharmalign_agent_common.spec import AgentSpec
from pharmalign_agent_common.vertex_schema import VertexSchemaGemini, vertex_schema


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor: str = Field(json_schema_extra={"examples": ["E-S25:v3:c0412"]})
    quote: str


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["message"] = "message"
    text: str
    citations: list[Evidence] = []


class NoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["no_source"] = "no_source"
    detail: str | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[Annotated[Union[Message, NoSource], Field(discriminator="type")]]


def vertex_client() -> Client:
    return Client(vertexai=True, project="p", location="global")


def test_the_union_as_written_is_rejected_by_the_vertex_conversion():
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        transformers.t_schema(vertex_client(), Answer)


def test_the_rewritten_schema_passes_the_conversion():
    assert transformers.t_schema(vertex_client(), vertex_schema(Answer)) is not None


def test_nothing_is_lost_in_the_rewrite():
    text = str(vertex_schema(Answer))
    for gone in ("$ref", "$defs", "oneOf", "discriminator", "const", "examples"):
        assert gone not in text
    members = vertex_schema(Answer)["properties"]["events"]["items"]["anyOf"]
    assert [m["properties"]["type"]["enum"] for m in members] == [["message"], ["no_source"]]  # each member keeps its tag
    assert members[0]["properties"]["citations"]["items"]["properties"]["anchor"]["type"] == "string"  # a referenced model is inlined
    assert members[0]["required"] == ["text"] or "text" in members[0]["required"]


def test_a_property_that_is_named_like_a_keyword_is_kept():
    class Odd(BaseModel):
        examples: str
        const: int

    assert set(vertex_schema(Odd)["properties"]) == {"examples", "const"}


def test_a_recursive_schema_is_refused_not_looped():
    class Node(BaseModel):
        child: "Node | None" = None

    with pytest.raises(ValueError, match="recursive"):
        vertex_schema(Node)


def test_the_model_rewrites_the_request_schema():
    seen = {}

    async def fake_super(self, request, stream=False):
        seen["schema"] = request.config.response_schema
        yield None

    model = VertexSchemaGemini(model="gemini-3.8-flash")
    request = LlmRequest(model="gemini-3.8-flash", config=types.GenerateContentConfig(response_schema=Answer))

    async def run():
        original = Gemini.generate_content_async
        Gemini.generate_content_async = fake_super
        try:
            async for _ in model.generate_content_async(request):
                pass
        finally:
            Gemini.generate_content_async = original

    asyncio.run(run())
    assert isinstance(seen["schema"], dict) and "anyOf" in str(seen["schema"])


def test_vertex_mode_builds_the_rewriting_model_and_api_key_mode_does_not():
    spec = AgentSpec.__new__(AgentSpec)
    object.__setattr__(spec, "name", "x")
    object.__setattr__(spec, "default_model", "gemini-3.8-flash")
    assert isinstance(build_model(spec, Settings(model_mode="vertex")), VertexSchemaGemini)
    plain = build_model(spec, Settings(model_mode="api_key"))
    assert isinstance(plain, Gemini) and not isinstance(plain, VertexSchemaGemini)
