"""remove unused FETCHED crawl_page_state value

Revision ID: 015_remove_unused_crawl_page_fetched
Revises: 014_extraction_run_resume_count
Create Date: 2026-06-18

Removes the dead ``FETCHED`` label from the
``crawl_page_state`` PostgreSQL enum. No current code writes or reads
this state. As a safety measure, any legacy rows still holding
``FETCHED`` are normalized to ``EXTRACTED`` before the enum type is
rebuilt.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "015_remove_unused_crawl_page_fetched"
down_revision: Union[str, None] = "014_extraction_run_resume_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE crawl_pages SET state = 'EXTRACTED' "
        "WHERE state = 'FETCHED'"
    )
    op.execute("ALTER TYPE crawl_page_state RENAME TO crawl_page_state_old")
    op.execute(
        "CREATE TYPE crawl_page_state AS ENUM "
        "('PENDING','FETCHING','EXTRACTED','BLOCKED','FAILED')"
    )
    op.execute("ALTER TABLE crawl_pages ALTER COLUMN state DROP DEFAULT")
    op.execute(
        "ALTER TABLE crawl_pages ALTER COLUMN state TYPE crawl_page_state "
        "USING state::text::crawl_page_state"
    )
    op.execute(
        "ALTER TABLE crawl_pages ALTER COLUMN state "
        "SET DEFAULT 'PENDING'"
    )
    op.execute("DROP TYPE crawl_page_state_old")


def downgrade() -> None:
    op.execute("ALTER TYPE crawl_page_state RENAME TO crawl_page_state_new")
    op.execute(
        "CREATE TYPE crawl_page_state AS ENUM "
        "('PENDING','FETCHING','FETCHED','EXTRACTED','BLOCKED','FAILED')"
    )
    op.execute("ALTER TABLE crawl_pages ALTER COLUMN state DROP DEFAULT")
    op.execute(
        "ALTER TABLE crawl_pages ALTER COLUMN state TYPE crawl_page_state "
        "USING state::text::crawl_page_state"
    )
    op.execute(
        "ALTER TABLE crawl_pages ALTER COLUMN state "
        "SET DEFAULT 'PENDING'"
    )
    op.execute("DROP TYPE crawl_page_state_new")
