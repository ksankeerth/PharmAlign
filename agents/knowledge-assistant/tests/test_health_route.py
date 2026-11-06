def test_the_app_serves_the_session_routes_and_one_health():
    from app.fast_api_app import app
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/health").json() == {"status": "ok"}
    openapi = app.openapi()["paths"]
    assert list(openapi).count("/health") == 1
    assert [getattr(r, "path", None) for r in app.router.routes].count("/health") == 1
    assert "post" in openapi["/sessions"]
    assert "post" in openapi["/sessions/{session_id}/turns"]
