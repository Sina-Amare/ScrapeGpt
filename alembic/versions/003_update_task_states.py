"""Update task states and add new columns

Revision ID: 003
Revises: 002
Create Date: 2024-12-27

Note: PostgreSQL requires new enum values to be committed before use.
We use separate execute calls and raw SQL for the index.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new enum values (each in separate statement)
    # Using IF NOT EXISTS for idempotency
    op.execute("ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'SCRAPING'")
    op.execute("COMMIT")  # Commit enum changes

    op.execute("ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'LLM_PROCESSING'")
    op.execute("COMMIT")

    op.execute("ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'COMPLETED'")
    op.execute("COMMIT")

    op.execute("ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'FAILED'")
    op.execute("COMMIT")

    # Add error column for failure reasons
    op.execute(
        "ALTER TABLE scrape_tasks ADD COLUMN IF NOT EXISTS error TEXT"
    )

    # Add result column for LLM output (JSONB)
    op.execute(
        "ALTER TABLE scrape_tasks ADD COLUMN IF NOT EXISTS result JSONB"
    )

    # Update partial unique index to exclude terminal states
    op.execute("DROP INDEX IF EXISTS ix_one_active_task_per_user")
    op.execute("""
        CREATE UNIQUE INDEX ix_one_active_task_per_user
        ON scrape_tasks (user_id)
        WHERE state NOT IN ('COMPLETED', 'FAILED')
    """)


def downgrade() -> None:
    # Restore old index
    op.execute("DROP INDEX IF EXISTS ix_one_active_task_per_user")
    op.execute("""
        CREATE UNIQUE INDEX ix_one_active_task_per_user
        ON scrape_tasks (user_id)
        WHERE state != 'FINALIZED'
    """)

    # Drop new columns
    op.execute("ALTER TABLE scrape_tasks DROP COLUMN IF EXISTS result")
    op.execute("ALTER TABLE scrape_tasks DROP COLUMN IF EXISTS error")

    # Note: Cannot remove enum values in PostgreSQL

