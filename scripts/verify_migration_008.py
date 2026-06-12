"""One-shot migration verification: print the current alembic revision,
the extraction_specs columns, the backfill count, and the
frontier_previews row count. Used to validate Phase 2.5 migration 008
against the project's real PostgreSQL instance.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:password@localhost:5432/scrapegpt",
    )
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"alembic_version: {rev}")

        cols = (
            await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name='extraction_specs' "
                    "AND column_name IN ('crawl_scope', 'quality_summary') "
                    "ORDER BY column_name"
                )
            )
        ).all()
        print(f"extraction_specs columns: {cols}")

        backfilled = (
            await conn.execute(
                text("SELECT COUNT(*) FROM extraction_specs WHERE crawl_scope IS NOT NULL")
            )
        ).scalar()
        print(f"specs_with_crawl_scope: {backfilled}")

        frontier_count = (
            await conn.execute(text("SELECT COUNT(*) FROM frontier_previews"))
        ).scalar()
        print(f"frontier_previews_rows: {frontier_count}")

        # Sample one backfilled scope so we can see the literal JSON.
        sample = (
            await conn.execute(
                text(
                    "SELECT id, crawl_scope FROM extraction_specs "
                    "WHERE crawl_scope IS NOT NULL LIMIT 1"
                )
            )
        ).first()
        if sample is not None:
            print(f"sample_spec_id: {sample[0]}")
            print(f"sample_crawl_scope: {sample[1]}")


if __name__ == "__main__":
    asyncio.run(main())
