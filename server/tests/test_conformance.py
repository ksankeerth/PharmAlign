"""The server serves exactly what the contract says."""
import re

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.routing import APIRoute
from pharmalign_contracts.server.routes import ROUTES, SERVER_API
from pharmalign_contracts.spec import build_openapi

from app.db import Base
from app.main import create_app
from app.ops import HANDLERS, OPERATIONS, op

from .conftest import SERVICE, FixedIdentity, client_for

ALL = [r for routes in ROUTES.values() for r in routes]


def _concrete(path: str) -> str:
    return re.sub(r"{(\w+)}", r"\1-1", path)


def test_every_operation_is_registered(make_app):
    app = make_app()
    registered = {(next(iter(r.methods)), r.path): r for r in app.routes if isinstance(r, APIRoute)}
    assert set(registered) == {(r.method, r.path) for r in ALL}
    for route in ALL:
        api_route = registered[(route.method, route.path)]
        assert api_route.methods == {route.method}
        assert api_route.status_code == route.status
        assert api_route.endpoint.contract is route


def test_openapi_is_the_contract(make_app):
    assert make_app().openapi() == build_openapi(SERVER_API)


async def test_every_operation_answers(make_app, capsys):
    identity = FixedIdentity()
    app = make_app(identity=identity)
    implemented = unbound = 0
    async with client_for(app) as client:
        for route in ALL:
            identity.view = route.views[0] if route.views else "platform_admin"
            headers = SERVICE if not route.views and not route.public else {}
            payload = {"json": {}} if route.body else {}
            response = await client.request(route.method, _concrete(route.path), headers=headers, **payload)
            if (route.method, route.path) in HANDLERS:
                implemented += 1
                # A made-up id may be a 404 about that thing. A 404 about the route itself is a miss.
                assert response.status_code not in (405, 501) and response.status_code < 500, (route.method, route.path, response.text)
                assert "is not part of this API" not in response.text, (route.method, route.path)
            else:
                unbound += 1
                assert response.status_code == 501, (route.method, route.path, response.text)
                assert response.json()["code"] == "not_implemented"
                assert response.headers["content-type"] == "application/problem+json"
                assert route.summary in response.json()["detail"]
    with capsys.disabled():
        print(f"\noperations: {len(ALL)} total, {implemented} implemented, {unbound} return 501")
    assert implemented + unbound == len(ALL) == len(OPERATIONS)


async def test_fixed_paths_beat_parameters(make_app):
    identity = FixedIdentity("platform_admin")
    async with client_for(make_app(identity=identity)) as client:
        response = await client.get("/api/v1/simulations/compare?governed=a&ungoverned=b")
    assert response.status_code == 404 and "Comparison" in response.json()["detail"]


def test_op_refuses_what_the_contract_lacks():
    async def handler(req, ctx): ...

    for method, path in (("GET", "/api/v1/nope"), ("POST", "/api/v1/me")):
        try:
            op(method, path)(handler)
        except ValueError:
            continue
        raise AssertionError(f"{method} {path} was bound")


async def test_migrations_match_the_tables(engine):
    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []
