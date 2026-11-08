"""Serves the built web UI (web/dist) from the same origin as the API, so the browser needs no proxy and no cross-origin settings.

A file that exists is sent as it is. Any other GET that is not the API's gets index.html, so a client route such as /cases/CASE-1042
opens the app. Paths the server owns answer 404 as before, never the page.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

OWNED = frozenset({"api", "mock", "mcp", "internal", "health", "openapi.json", "docs", "redoc"})  # the first part of a path the server answers itself
IMMUTABLE = "public, max-age=31536000, immutable"  # Vite names built files by their content
NO_STORE = "no-store"


def _owned(path: str) -> bool:
    return path.split("/", 1)[0] in OWNED


def mount_web(app: FastAPI, directory: str) -> None:
    """Add the UI routes. Call it last, after every API route and mount, so those win."""
    root = Path(directory).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"PHARMALIGN_WEB_DIR is {directory}, which has no index.html. Build the web UI first.")

    @app.get("/{path:path}", include_in_schema=False)
    async def ui(path: str) -> Response:
        if _owned(path):
            raise HTTPException(404)
        target = (root / path).resolve()
        if path and target.is_file() and root in target.parents:  # nothing outside the folder, whatever the path says
            return FileResponse(target, headers={"Cache-Control": IMMUTABLE if path.startswith("assets/") else NO_STORE})
        if "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(404)  # a missing file (a script, an image) is not a client route
        return FileResponse(index, headers={"Cache-Control": NO_STORE})
