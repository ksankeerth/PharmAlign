"""Gives every chunk in a database a vector from the configured embedder, for the chunks that have none yet.

    PHARMALIGN_DATABASE_URL=postgresql+asyncpg://... PHARMALIGN_EMBEDDER=gemini-embedding-001 \\
        python scripts/embed_library.py [--batch 512] [--limit 2000]

Safe to stop and start again: a chunk that already has a vector for this model is skipped. Each batch is its own transaction.
Run it from the server folder (python -I is fine; it reads no document files).
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import make_sessions  # noqa: E402
from app.knowledge.embedding import get_embedder  # noqa: E402
from app.knowledge.indexing import embed_missing  # noqa: E402


async def main(batch: int, limit: int | None) -> int:
    settings = Settings()
    embedder = get_embedder(settings)
    engine = create_async_engine(settings.database_url)
    sessions = make_sessions(engine)
    async with sessions() as db:
        total = (await db.execute(text("SELECT count(*) FROM chunk"))).scalar_one()
        have = (await db.execute(text("SELECT count(*) FROM chunk_embedding WHERE model = :m"), {"m": embedder.name}), )[0].scalar_one()
    print(f"{embedder.name}: {have} of {total} chunks have a vector. Embedding the rest.", flush=True)
    done, started = 0, time.time()
    while limit is None or done < limit:
        async with sessions() as db, db.begin():
            n = await embed_missing(db, embedder, limit=min(batch, limit - done) if limit else batch)
        if n == 0:
            break
        done += n
        print(f"  {done} written, {done / (time.time() - started):.1f} chunks/s", flush=True)
    await engine.dispose()
    print(f"Done: {done} vectors in {time.time() - started:.0f}s.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", type=int, default=512, help="chunks per transaction")
    parser.add_argument("--limit", type=int, help="stop after this many (to try it out)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.batch, args.limit)))
