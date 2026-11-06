from tests.conftest import READER


def test_the_app_serves_the_contract_routes_and_one_health():
    from app.fast_api_app import app
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/health").json() == {"status": "ok"}
    paths = [getattr(r, "path", None) for r in app.router.routes]
    assert paths.count("/health") <= 1
    openapi = app.openapi()["paths"]
    assert list(openapi).count("/health") == 1
    assert "post" in openapi[READER.path]
    assert {"get", "delete"} <= set(openapi[READER.path + "/{run_id}"])
