"""Puts the real text of the core documents into the demo library (simulation only).

    PHARMALIGN_ENV=simulation uv run python -m app.seed --reset --with-library

After the normal seed, each document whose file is present is added as a new version of the seeded document and ingested through the
server's own routes and worker: parse, quality gate, chunks, embeddings. A second demo person approves it, the country adopts it as
in force, and a new snapshot becomes the workspace's active one. The seeded cases keep the snapshot they were pinned to.
A file that is missing, or that the gate refuses, is skipped with a line, and the seed still succeeds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .config import Settings
from .db import make_sessions
from .knowledge.presets import PRESETS
from .stores import STORES

TENANT = "ws-ae"
UPLOADER, REVIEWER = "p-jordan", "p-sam"  # two distinct people: nobody approves what they added
ALWAYS = ("A-01", "A-03", "A-12")
LIBRARY_DIR = Path(os.environ.get("PHARMALIGN_LIBRARY_DIR") or Path(__file__).resolve().parents[2] / "knowledge" / "documents")
PRESET_FOR = {"law": "law", "cabinet_resolution": "law", "executive_regulation": "law", "service_card": "web_page", "procedure": "web_page"}
EXTENSIONS = (".pdf", ".html", ".txt")


def cited_documents(seed_dir: Path) -> list[str]:
    """The documents the seeded packs name as a check's source."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("doc_id"), str):
                found.add(node["doc_id"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    path = seed_dir / "pack_version.json"
    if path.exists():
        walk(json.loads(path.read_text()))
    return sorted(found | set(ALWAYS))


def find_files(root: Path, ids: list[str]) -> dict[str, Optional[Path]]:
    """`<root>/<folder>/<id>__<name>.<ext>`. A PDF is preferred over other formats of the same document."""
    out: dict[str, Optional[Path]] = {}
    for doc_id in ids:
        matches = sorted(root.glob(f"*/{doc_id}__*")) if root.is_dir() else []
        matches = [p for p in matches if p.suffix.lower() in EXTENSIONS]
        out[doc_id] = min(matches, key=lambda p: EXTENSIONS.index(p.suffix.lower())) if matches else None
    return out


def profile_for(doc_type: str) -> dict:
    preset = next(p for p in PRESETS if p.key == PRESET_FOR.get(doc_type, "guideline"))
    return preset.profile.model_copy(update={"parser": "fast_text"}).model_dump(mode="json")


def as_person(person: str) -> dict:
    return {"Authorization": f"Bearer {person}"}


async def load_library(settings: Settings, files: Optional[dict[str, Optional[Path]]] = None, say: Callable[[str], None] = print) -> dict:
    """Adds the real files. `files` maps a document id to its file (None: absent). Returns what was done, for a report and for tests."""
    from .main import create_app
    from .jobs import Worker

    if settings.env != "simulation":
        raise RuntimeError("The library seed needs PHARMALIGN_ENV=simulation.")
    if files is None:
        from .seed import SEED_DIR

        files = find_files(LIBRARY_DIR, cited_documents(SEED_DIR))
    app = create_app(settings=settings.model_copy(update={"worker_enabled": False}))
    result: dict = {"ingested": {}, "skipped": {}, "snapshot_id": None, "retrieval": None}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://library", timeout=600) as http:
            worker = Worker(app.state.rt)

            async def call(method: str, path: str, person: str, **kw) -> httpx.Response:
                return await http.request(method, path, headers=as_person(person), **kw)

            async def finish(job_id: str) -> dict:
                """Runs the worker until the job ends. An embed step takes a batch, so a long document takes many steps."""
                for _ in range(2000):
                    job = (await call("GET", f"/api/v1/ingest-jobs/{job_id}", UPLOADER)).json()
                    if job["status"] in ("completed", "failed", "cancelled"):
                        return job
                    if not await worker.run_due(limit=50):
                        break
                return (await call("GET", f"/api/v1/ingest-jobs/{job_id}", UPLOADER)).json()

            async def skip(doc_id: str, why: str) -> None:
                result["skipped"][doc_id] = why
                say(f"  library: skipped {doc_id}: {why}")

            for doc_id, path in files.items():
                if path is None or not Path(path).is_file():
                    await skip(doc_id, "file not found")
                    continue
                doc = await call("GET", f"/api/v1/documents/{doc_id}", UPLOADER)
                if doc.status_code != 200:
                    await skip(doc_id, "not in the seeded library")
                    continue
                previous = doc.json()["latest_version_id"]
                up = await call("POST", f"/api/v1/documents/{doc_id}/versions", UPLOADER, files={"file": (Path(path).name, Path(path).read_bytes())},
                                data={"version_label": "Official text"})
                if up.status_code != 201:
                    await skip(doc_id, up.json().get("title", f"upload refused ({up.status_code})"))
                    continue
                version = up.json()["id"]
                profile = profile_for(doc.json()["doc_type"])
                trial = await call("POST", f"/api/v1/document-versions/{version}/ingest-jobs", UPLOADER, json={"mode": "trial", "profile": profile})
                job = await finish(trial.json()["id"]) if trial.status_code == 202 else {"status": "failed", "error": trial.text[:200]}
                if job["status"] != "completed":
                    await skip(doc_id, f"trial ingestion {job['status']}: {job.get('error')}")
                    continue
                review = await call("POST", f"/api/v1/document-versions/{version}/review", REVIEWER, json={"decision": "approved", "notes": None})
                if review.status_code != 201:
                    await skip(doc_id, f"review refused ({review.status_code})")
                    continue
                full = await call("POST", f"/api/v1/document-versions/{version}/ingest-jobs", UPLOADER, json={"mode": "full", "profile": profile})
                job = await finish(full.json()["id"]) if full.status_code == 202 else {"status": "failed", "error": full.text[:200]}
                if job["status"] != "completed":
                    await skip(doc_id, f"ingestion {job['status']}: {job.get('error')}")
                    continue
                # The country adopts the new text in force at the level the old copy had. The old copy is superseded, in the new snapshot only.
                async with app.state.rt.sessions() as db:
                    from .seed import _ctx

                    old = await STORES["tenant_document_adoption"].get(_ctx(db, TENANT), f"{TENANT}|{previous}")
                level = old.authority_level if old else 1
                put = await call("PUT", f"/api/v1/workspaces/{TENANT}/adoptions/{version}", REVIEWER,
                                 json={"adopted": True, "legal_status": "in_force", "authority_level": level, "notes": "Official text."})
                assert put.status_code == 200, put.text
                if old:
                    await call("PUT", f"/api/v1/workspaces/{TENANT}/adoptions/{previous}", REVIEWER,
                               json={"adopted": True, "legal_status": "superseded", "authority_level": level, "notes": "Replaced by the official text."})
                result["ingested"][doc_id] = job["chunks"]
                say(f"  library: {doc_id} ingested as {version}, {job['chunks']} chunks")

            if not result["ingested"]:
                say("  library: nothing ingested, so the active snapshot stays as it was")
                return result
            snap = await call("POST", f"/api/v1/workspaces/{TENANT}/kb-snapshots", REVIEWER, json={"label": "Library with official texts", "note": None})
            assert snap.status_code == 201, snap.text
            result["snapshot_id"] = snap.json()["id"]
            async with app.state.rt.sessions() as db, db.begin():
                from .seed import _ctx

                ctx = _ctx(db, TENANT)
                workspace = await STORES["workspace"].require(ctx, TENANT, "Workspace")
                await STORES["workspace"].save(ctx, workspace.model_copy(update={"active_kb_snapshot_id": result["snapshot_id"]}))
            say(f"  library: snapshot {result['snapshot_id']} is now active")
            result["retrieval"] = await retrieval_check(app, result["snapshot_id"])
            say(f"  library: retrieval check on the new snapshot: {result['retrieval']['detail']} ({result['retrieval']['status']})")
    finally:
        await app.state.rt.engine.dispose()
    return result


async def retrieval_check(app, snapshot_id: str) -> dict:
    """The readiness question set, run on a snapshot."""
    from .knowledge.embedding import get_embedder
    from .knowledge.retrieval import QUESTION_SETS, run_questions, verdict
    from .knowledge.search import Scope

    async with app.state.rt.sessions() as db:
        embedder = get_embedder(app.state.rt.settings)
        run = await run_questions(db, Scope(TENANT, "AE", snapshot_id, "requirement"), embedder, QUESTION_SETS["core-questions"])
    status, detail = verdict(run, embedder.name)
    return {"status": status, "detail": detail, "hit_at_5": run.document_hit_at_5, "results": [r.model_dump() for r in run.results]}
