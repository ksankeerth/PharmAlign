"""With PHARMALIGN_WEB_DIR set, the server serves the built UI from the same origin, and the API keeps its own paths."""
import pytest

from app.main import create_app
from app.config import Settings

from .conftest import client_for


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>PharmAlign</title><div id=root></div>")
    (tmp_path / "assets" / "app-1a2b3c.js").write_text("console.log('ui')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("outside the folder")
    return tmp_path


@pytest.fixture
async def http(make_app, dist):
    app = make_app(settings={"web_dir": str(dist)})
    async with client_for(app) as client:
        yield client


async def test_the_root_and_client_routes_open_the_app(http):
    for path in ("/", "/cases/CASE-1042", "/admin/agent-health", "/watch/notices/n-1"):
        r = await http.get(path)
        assert r.status_code == 200 and "<div id=root>" in r.text and r.headers["cache-control"] == "no-store", path


async def test_built_files_are_sent_as_they_are_and_cached_by_name(http):
    r = await http.get("/assets/app-1a2b3c.js")
    assert r.status_code == 200 and r.text == "console.log('ui')" and "immutable" in r.headers["cache-control"]
    assert (await http.get("/favicon.svg")).text == "<svg/>"


async def test_a_missing_file_is_not_a_client_route(http):
    assert (await http.get("/assets/missing.js")).status_code == 404
    assert (await http.get("/nope.png")).status_code == 404


async def test_a_path_cannot_climb_out_of_the_folder(http):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        r = await http.get(path)
        assert "outside the folder" not in r.text, path


async def test_the_api_the_health_check_and_the_tool_server_keep_their_paths(http):
    assert (await http.get("/health")).json() == {"status": "ok"}
    assert (await http.get("/api/v1/admin/agents/health", headers={"Authorization": "Bearer p-jordan"})).headers["content-type"].startswith("application/json")
    r = await http.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert "<div id=root>" not in (await http.get("/mock/anything")).text
    assert (await http.get("/internal/ticks")).status_code in (404, 405)
    assert "<div id=root>" not in (await http.get("/mcp")).text


async def test_without_a_web_dir_the_server_serves_no_page(make_app):
    async with client_for(make_app()) as http:
        assert (await http.get("/")).status_code == 404


def test_a_web_dir_with_no_build_stops_the_server(tmp_path, engine):
    with pytest.raises(FileNotFoundError, match="index.html"):
        create_app(settings=Settings(env="simulation", web_dir=str(tmp_path), worker_enabled=False))
