"""Result schemas that Vertex AI accepts.

Vertex's `Schema` takes a fixed list of keywords. A result model that holds a union of event types (a Pydantic discriminated union)
writes `oneOf`, `discriminator`, `const`, `$ref` and `examples`, and the Google client rejects the request before it is sent
("Extra inputs are not permitted"), so every turn of that agent failed. The API-key endpoint does not run that check.

`vertex_schema` rewrites a JSON schema into the form Vertex takes, keeping what it says:
- `$ref` is inlined, so no definition is lost.
- `oneOf` becomes `anyOf`, and `const: x` becomes `enum: [x]`, so each member keeps its `type` tag.
- `discriminator` and `examples`, and any other keyword Vertex doesn't take, are dropped. Property names are never touched.

`VertexSchemaGemini` applies this to the request's `response_schema`. It is used only in Vertex mode, so the API-key path is unchanged.
"""
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

_ALLOWED = {name for name in types.Schema.model_fields} | {f.alias for f in types.Schema.model_fields.values() if f.alias}
_MAX_DEPTH = 32


def _inline(node: Any, defs: dict, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise ValueError("a recursive schema can't be written for Vertex")
    if isinstance(node, dict):
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            return {**_inline(target, defs, depth + 1), **{k: v for k, v in node.items() if k != "$ref"}}
        return {k: _inline(v, defs, depth) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline(item, defs, depth) for item in node]
    return node


def _clean(node: Any, in_properties: bool = False) -> Any:
    if isinstance(node, dict):
        out: dict = {}
        for key, value in node.items():
            if in_properties:  # a map of property name to schema: the names are data
                out[key] = _clean(value)
            elif key == "const":
                out["enum"] = [value]
            else:
                name = "anyOf" if key == "oneOf" else key
                if name in _ALLOWED and name != "discriminator":
                    out[name] = _clean(value, in_properties=key == "properties")
        return out
    if isinstance(node, list):
        return [_clean(item) for item in node]
    return node


def vertex_schema(schema: type[BaseModel] | dict) -> dict:
    """A JSON schema, written with only the keywords Vertex takes."""
    raw = schema.model_json_schema() if isinstance(schema, type) else schema
    return _clean(_inline(raw, raw.get("$defs", {})))


class VertexSchemaGemini(Gemini):
    """Gemini whose result schema is rewritten for Vertex before each request."""

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        config = llm_request.config
        schema = config.response_schema if config else None
        if isinstance(schema, dict) or (isinstance(schema, type) and issubclass(schema, BaseModel)):
            config.response_schema = vertex_schema(schema)
        async for response in super().generate_content_async(llm_request, stream=stream):
            yield response
