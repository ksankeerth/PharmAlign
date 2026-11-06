"""Turns a table of routes and Pydantic models into an OpenAPI 3.1 document."""
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import models_json_schema

from .common import CONTRACT_HEADER, Problem

REF = "#/components/schemas/{model}"


@dataclass(frozen=True)
class Q:
    """A query parameter."""

    name: str
    description: str = ""
    type: str = "string"
    enum: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class R:
    """One operation."""

    method: str
    path: str
    summary: str
    response: Optional[type[BaseModel]] = None
    status: int = 200
    body: Optional[type[BaseModel]] = None
    forms: tuple[tuple[str, type[BaseModel]], ...] = ()  # extra (media type, model) request bodies
    query: tuple[Q, ...] = ()
    views: tuple[str, ...] = ()
    guarded: bool = False  # 409 lists the failed guards
    conflict: Optional[str] = None  # 409 for another reason
    idempotent: bool = False  # accepts Idempotency-Key
    if_match: bool = False
    simulation_only: bool = False
    public: bool = False
    description: str = ""
    stream_schema: Optional[str] = None
    extra_errors: tuple[tuple[int, str], ...] = ()
    request_example: Optional[dict] = None
    response_example: Optional[dict] = None
    actor: Optional[str] = None  # a non-person caller, such as the connector or scheduler


@dataclass
class ApiDoc:
    title: str
    version: str
    description: str
    routes: dict[str, list[R]]  # by tag
    problem_models: tuple[type[BaseModel], ...] = (Problem,)
    guard_model: Optional[type[BaseModel]] = None
    security_description: str = "Session token."
    cookie_name: Optional[str] = None  # a session cookie the UI sends instead of a token
    key_reuse_422: bool = False  # a reused Idempotency-Key with a different request returns 422
    servers: tuple[str, ...] = ()
    contract_header: bool = False
    streams: dict[str, Any] = field(default_factory=dict)  # schema name -> type adapter


def _op_id(route: R) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", route.path).strip("_")
    return f"{route.method.lower()}_{slug or 'root'}"


def _problem(description: str, schema: str = "Problem") -> dict:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def _operation(route: R, doc: ApiDoc, refs: dict) -> dict:
    op: dict[str, Any] = {"summary": route.summary, "operationId": _op_id(route)}
    if route.description:
        op["description"] = route.description

    params: list[dict] = []
    for name in re.findall(r"{(\w+)}", route.path):
        params.append({"name": name, "in": "path", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}})
    for q in route.query:
        schema: dict[str, Any] = {"type": q.type}
        if q.enum:
            schema["enum"] = list(q.enum)
        param = {"name": q.name, "in": "query", "required": q.required, "schema": schema}
        if q.description:
            param["description"] = q.description
        params.append(param)
    if route.idempotent:
        params.append({
            "name": "Idempotency-Key", "in": "header", "required": False, "schema": {"type": "string", "maxLength": 128},
            "description": "Repeating a request with the same key returns the first result and changes nothing.",
        })
    if route.if_match:
        params.append({
            "name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"},
            "description": "The content hash you last read. A mismatch returns 412.",
        })
    if doc.contract_header:
        params.append({"name": CONTRACT_HEADER, "in": "header", "required": True, "schema": {"type": "string"}})
    if params:
        op["parameters"] = params

    bodies = ([("application/json", route.body)] if route.body else []) + list(route.forms)
    if bodies:
        content = {}
        for media, model in bodies:
            content[media] = {"schema": refs[model]}
            if route.request_example and media == "application/json":
                content[media]["example"] = route.request_example
        op["requestBody"] = {"required": True, "content": content}

    responses: dict[str, Any] = {}
    if route.stream_schema:
        responses[str(route.status)] = {
            "description": "A stream of server-sent events.",
            "content": {"text/event-stream": {"schema": {"$ref": f"#/components/schemas/{route.stream_schema}"}}},
        }
    elif route.response:
        content = {"schema": refs[route.response]}
        if route.response_example:
            content["example"] = route.response_example
        responses[str(route.status)] = {"description": "Success.", "content": {"application/json": content}}
    else:
        responses[str(route.status)] = {"description": "Success."}

    if not route.public:
        responses["401"] = _problem("Not signed in.")
        responses["403"] = _problem("This view may not do this.")
    if re.search(r"{\w+}", route.path):
        responses["404"] = _problem("Not found.")
    if route.guarded:
        responses["409"] = _problem("Refused. Lists every guard that failed.", doc.guard_model.__name__)
    elif route.conflict:
        responses["409"] = _problem(route.conflict)
    if route.if_match:
        responses["412"] = _problem("Someone else changed this first. Read it again.")
    reuse = route.idempotent and doc.key_reuse_422
    if bodies or route.query or reuse:
        responses["422"] = _problem(
            "The request breaks the contract." + (" `idempotency_key_reused` means the key was used for a different request." if reuse else "")
        )
    for code, description in route.extra_errors:
        responses[str(code)] = _problem(description)
    op["responses"] = dict(sorted(responses.items()))

    if not route.public:
        # An operation with no views is for a service caller, which sends the service token only.
        op["security"] = [{"bearerAuth": []}]
        if doc.cookie_name and route.views:
            op["security"].append({"cookieAuth": []})
    if route.views:
        op["x-allowed-views"] = list(route.views)
    if route.actor:
        op["x-caller"] = route.actor
    if route.simulation_only:
        op["x-simulation-only"] = True
    return op


def build_openapi(doc: ApiDoc) -> dict:
    models: list[type[BaseModel]] = list(doc.problem_models)
    if doc.guard_model:
        models.append(doc.guard_model)
    for routes in doc.routes.values():
        for route in routes:
            models += [m for m in (route.response, route.body) if m]
            models += [m for _, m in route.forms]
    models = list(dict.fromkeys(models))

    mapping, defs = models_json_schema([(m, "validation") for m in models], ref_template=REF)
    refs = {m: mapping[(m, "validation")] for m in models}
    schemas = defs.get("$defs", {})

    for name, adapter in doc.streams.items():
        schema = adapter.json_schema(ref_template=REF)
        schemas.update(schema.pop("$defs", {}))
        schema["description"] = "One server-sent event."
        schemas[name] = schema

    paths: dict[str, dict] = {}
    tags = []
    for tag, routes in doc.routes.items():
        tags.append({"name": tag})
        for route in routes:
            op = _operation(route, doc, refs)
            op["tags"] = [tag]
            paths.setdefault(route.path, {})[route.method.lower()] = op

    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": doc.title, "version": doc.version, "description": doc.description},
    }
    if doc.servers:
        spec["servers"] = [{"url": url} for url in doc.servers]
    spec["tags"] = tags
    spec["paths"] = paths
    schemes: dict[str, Any] = {"bearerAuth": {"type": "http", "scheme": "bearer", "description": doc.security_description}}
    if doc.cookie_name:
        schemes["cookieAuth"] = {
            "type": "apiKey", "in": "cookie", "name": doc.cookie_name,
            "description": "Session cookie, as the web UI sends it. Accepted wherever a bearer token is, except on service-only operations.",
        }
    spec["components"] = {"schemas": dict(sorted(schemas.items())), "securitySchemes": schemes}
    return spec


__all__ = ["ApiDoc", "Q", "R", "TypeAdapter", "build_openapi"]
