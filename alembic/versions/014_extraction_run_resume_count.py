"""extraction_runs.resume_count: bound watchdog crash-loop resume

Revision ID: 014_extraction_run_resume_count
Revises: 013_extraction_runs
Create Date: 2026-06-17

Adds ``extraction_runs.resume_count`` so the watchdog can re-dispatch a stalled
(crashed in-process worker) extraction run a bounded number of times before
hard-failing the project (A1). Existing rows default to 0.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014_extraction_run_resume_count"
down_revision: Union[str, None] = "013_extraction_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_runs",
        sa.Column(
            "resume_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("extraction_runs", "resume_count")
