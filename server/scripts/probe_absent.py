"""Check that a no-source question's key terms appear in no chunk of the loaded library.

    PHARMALIGN_ENV=simulation uv run python scripts/probe_absent.py homeopathic radiopharmaceutical ...

Case-insensitive; prints term counts only, never chunk text.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.config import Settings
from app.db import make_engine


async def main(words: list[str]) -> int:
    engine = make_engine(Settings().database_url)
    found = 0
    async with engine.connect() as c:
        total = (await c.execute(text("select count(*) from chunk"))).scalar_one()
        print(f"chunks searched: {total}")
        for w in words:
            n = (await c.execute(text("select count(*) from chunk where lower(text) like :p"), {"p": f"%{w.lower()}%"})).scalar_one()
            print(f"{w!r}: {n} chunks")
            found += n
    await engine.dispose()
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
