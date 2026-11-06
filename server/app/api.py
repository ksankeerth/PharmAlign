"""Generates the FastAPI routes from the contract, and runs each request as one unit of work."""
import json
import logging
import secrets
from typing import Any, Optional

import asyncpg
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from pharmalign_contracts.common import Problem
from pharmalign_contracts.server.routes import SERVER_API
from pharmalign_contracts.spec import Q, R, build_openapi
from pydantic import ValidationError
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from . import authz, idempotency
from .context import Ctx, Runtime, Tenant
from .identity import Actor, bearer_token
from .ops import OPERATIONS, Loose, Params, Req
from .problems import ApiError, invalid, problem, problem_response

log = logging.getLogger(__name__)

MAX_PATH_PARAM = 128
DB_DOWN = (OSError, OperationalError, InterfaceError, asyncpg.PostgresConnectionError)


def _summarise(error: ValidationError, where: str) -> str:
    # Field and message only: the values may be confidential.
    parts = [f"{'.'.join(str(p) for p in (where, *e['loc']))}: {e['msg']}" for e in error.errors()]
    return "; ".join(parts)


def _parse_query_value(q: Q, raw: str) -> Any:
    if q.enum and raw not in q.enum:
        invalid(f"{q.name} must be one of: {', '.join(q.enum)}.")
    if q.type == "integer":
        try:
            return int(raw)
        except ValueError:
            invalid(f"{q.name} must be a whole number.")
    if q.type == "boolean":
        if raw not in ("true", "false"):
            invalid(f"{q.name} must be true or false.")
        return raw == "true"
    return raw


def _parse_query(route: R, request: Request) -> Params:
    declared = {q.name: q for q in route.query}
    unknown = sorted(set(request.query_params) - set(declared))
    if unknown:
        invalid(f"Unknown query parameter: {', '.join(unknown)}.")
    query = Params()
    for name, q in declared.items():
        raw = request.query_params.get(name)
        if raw is None and q.required:
            invalid(f"{name} is required.")
        query[name] = None if raw is None else _parse_query_value(q, raw)
    return query


async def _parse_body(route: R, request: Request) -> Any:
    raw = await request.body()  # read first, so the idempotency hash can read it again after a form
    media = request.headers.get("content-type", "application/json").split(";")[0].strip().lower()
    for form_media, model in route.forms:
        if media == form_media:
            form = await request.form()
            values = {k: (await v.read()) if isinstance(v, UploadFile) else v for k, v in form.items()}
            try:
                return model.model_validate(values)
            except ValidationError as e:
                invalid(_summarise(e, "body"))
    if route.body is None:
        return None
    if media != "application/json":
        invalid(f"Send this as application/json, not {media}.")
    try:
        return route.body.model_validate_json(raw)
    except ValidationError as e:
        invalid(_summarise(e, "body"))


async def _parse(route: R, request: Request) -> Req:
    path = Params(request.path_params)
    for name, value in path.items():
        if len(value) > MAX_PATH_PARAM:
            invalid(f"{name} is longer than {MAX_PATH_PARAM} characters.")
    if_match = request.headers.get("if-match")
    if route.if_match and if_match is None:
        invalid("This operation needs an If-Match header.")
    return Req(path=path, query=_parse_query(route, request), body=await _parse_body(route, request), if_match=if_match)


async def _authenticate(rt: Runtime, db, request: Request) -> Optional[Actor]:
    token = bearer_token(request)
    expected = rt.settings.service_token
    if token and expected and secrets.compare_digest(token.encode(), expected.encode()):
        return Actor(type="system", id="service", tenant_id=rt.settings.default_tenant)
    return await rt.identity.authenticate(db, request, rt.clock.now())


def _encode(route: R, result: Any) -> Optional[str]:
    """Checks the handler's result against the contract, before anything commits."""
    if route.response is None:
        return None
    if isinstance(result, Loose):
        return json.dumps({**result.head.model_dump(mode="json"), **result.rest})
    return route.response.model_validate(result).model_dump_json()


def _reply(status: int, body: Optional[str], cookies: Optional[dict] = None, replayed: bool = False) -> Response:
    response = Response(content=body, status_code=status, media_type=None if body is None else "application/json")
    for name, value in (cookies or {}).items():
        if value is None:
            response.delete_cookie(name, httponly=True, samesite="lax", path="/")
        else:
            response.set_cookie(name, value, httponly=True, samesite="lax", path="/")
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return response


def _stream(route: R, events) -> StreamingResponse:
    adapter = SERVER_API.streams[route.stream_schema]

    async def frames():
        async for event in events:
            yield b"data: " + adapter.dump_json(adapter.validate_python(event)) + b"\n\n"

    return StreamingResponse(frames(), status_code=route.status, media_type="text/event-stream")


async def _unit_of_work(rt: Runtime, route: R, request: Request) -> Response:
    """One transaction: state, audit, events, jobs and the idempotency record commit together or not at all."""
    handler = rt.handlers.get((route.method, route.path))
    async with rt.sessions() as db, db.begin():
        actor = None if route.public else await _authenticate(rt, db, request)
        tenant = Tenant(actor.tenant_id, rt.settings.env) if actor else None
        authz.can_act(route, actor, tenant, rt.settings.env)
        if handler is None:
            problem(501, "not_implemented", "Not implemented yet", f"{route.summary} is not implemented.")
        req = await _parse(route, request)

        claim = None
        key = request.headers.get("idempotency-key") if route.idempotent else None
        if key:
            claim = idempotency.Claim(tenant.id, actor.id, f"{route.method} {route.path}", key)
            digest = idempotency.request_hash(request.url.path, request.url.query, await request.body())
            first = await idempotency.begin(db, claim, digest, rt.clock.now())
            if first:
                return _reply(first.status, first.body, replayed=True)

        ctx = Ctx(rt, db, actor, tenant)
        result = await handler(req, ctx)
        body = None if route.stream_schema else _encode(route, result)
        await ctx.flush()
        if claim:
            await idempotency.finish(db, claim, idempotency.Stored(route.status, body))
    # Committed. A stream's events are sent after this, so its handler must not use ctx.db in them.
    if route.stream_schema:
        return _stream(route, result)
    return _reply(route.status, body, ctx.cookies)


def _endpoint(route: R):
    async def endpoint(request: Request) -> Response:
        rt: Runtime = request.app.state.rt
        try:
            return await _unit_of_work(rt, route, request)
        except ApiError as e:
            return problem_response(e.body)
        except DB_DOWN:
            log.exception("%s %s: database unavailable", route.method, route.path)
            return problem_response(Problem(title="The database isn't available", status=503, code="database_unavailable"))
        except Exception:
            log.exception("%s %s failed", route.method, route.path)
            return problem_response(Problem(title="The server failed", status=500, code="internal_error"))

    endpoint.contract = route
    return endpoint


def _route_order(route: R) -> list[bool]:
    # A fixed segment must come before a parameter, or /simulations/{id} would swallow /simulations/compare.
    return [segment.startswith("{") for segment in route.path.split("/")]


def install_routes(app: FastAPI) -> None:
    """One FastAPI route per operation. Unbound ones answer 501. The OpenAPI document is the contract's own."""
    for route in sorted(OPERATIONS.values(), key=_route_order):
        app.add_api_route(route.path, _endpoint(route), methods=[route.method], status_code=route.status, summary=route.summary)
    document = build_openapi(SERVER_API)
    app.openapi = lambda: document


HTTP_CODES = {404: ("not_found", "Not found"), 405: ("method_not_allowed", "Method not allowed")}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, error: HTTPException) -> Response:
        code, title = HTTP_CODES.get(error.status_code, ("http_error", "Request failed"))
        detail = f"{request.method} {request.url.path} is not part of this API."[:500] if error.status_code in HTTP_CODES else None
        return problem_response(Problem(title=title, status=error.status_code, code=code, detail=detail))

    @app.exception_handler(Exception)
    async def failed(request: Request, error: Exception) -> Response:
        log.exception("%s %s failed", request.method, request.url.path, exc_info=error)
        return problem_response(Problem(title="The server failed", status=500, code="internal_error"))
