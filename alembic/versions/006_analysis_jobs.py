"""analysis jobs and cache tables

Revision ID: 006
Revises: 005
Create Date: 2026-06-07

Adds:
  - job_state, extraction_mode, workflow_mode, render_mode enum types
  - jobs table (analysis job tracking)
  - analysis_cache table (content-hash-keyed cache)

PostgreSQL CREATE TYPE cannot run inside a transaction, so enum creation
uses raw EXECUTE outside alembic's implicit transaction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enum types — must be created outside a transaction in PostgreSQL
    # ------------------------------------------------------------------
    op.execute("COMMIT")
    op.execute(
        "CREATE TYPE job_state AS ENUM "
        "('QUEUED','ANALYZING','AWAITING_SETUP','ANALYSIS_READY','FAILED','CANCELED')"
    )
    op.execute(
        "CREATE TYPE extraction_mode AS ENUM ('STRUCTURED','CONTENT')"
    )
    op.execute(
        "CREATE TYPE workflow_mode AS ENUM ('GUIDED','FAST')"
    )
    op.execute(
        "CREATE TYPE render_mode AS ENUM ('AUTO','STATIC','BROWSER')"
    )
    op.execute("BEGIN")

    # ------------------------------------------------------------------
    # jobs table
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_config_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("normalized_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "extraction_mode",
            postgresql.ENUM("STRUCTURED", "CONTENT", name="extraction_mode", create_type=False),
            nullable=False,
            server_default="STRUCTURED",
        ),
        sa.Column(
            "workflow_mode",
            postgresql.ENUM("GUIDED", "FAST", name="workflow_mode", create_type=False),
            nullable=False,
            server_default="GUIDED",
        ),
        sa.Column(
            "render_mode",
            postgresql.ENUM("AUTO", "STATIC", "BROWSER", name="render_mode", create_type=False),
            nullable=False,
            server_default="AUTO",
        ),
        sa.Column(
            "state",
            postgresql.ENUM(
                "QUEUED", "ANALYZING", "AWAITING_SETUP", "ANALYSIS_READY", "FAILED", "CANCELED",
                name="job_state",
                create_type=False,
            ),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=True,
        ),
        sa.Column(
            "analysis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "fetch_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["provider_configs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_user_id_created_at", "jobs", ["user_id", "created_at"])
    op.create_index("ix_jobs_user_id_state", "jobs", ["user_id", "state"])
    op.create_index("ix_jobs_state_updated_at", "jobs", ["state", "updated_at"])

    # ------------------------------------------------------------------
    # analysis_cache table
    # ------------------------------------------------------------------
    op.create_table(
        "analysis_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "extraction_mode",
            postgresql.ENUM("STRUCTURED", "CONTENT", name="extraction_mode", create_type=False),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("analyzer_version", sa.String(length=16), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("normalized_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_cache_lookup",
        "analysis_cache",
        ["content_hash", "extraction_mode", "provider", "model", "analyzer_version"],
        unique=True,
    )
    op.create_index(
        "ix_analysis_cache_content_hash",
        "analysis_cache",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_cache_content_hash", table_name="analysis_cache")
    op.drop_index("ix_analysis_cache_lookup", table_name="analysis_cache")
    op.drop_table("analysis_cache")

    op.drop_index("ix_jobs_state_updated_at", table_name="jobs")
    op.drop_index("ix_jobs_user_id_state", table_name="jobs")
    op.drop_index("ix_jobs_user_id_created_at", table_name="jobs")
    op.drop_table("jobs")

    op.execute("COMMIT")
    op.execute("DROP TYPE IF EXISTS render_mode")
    op.execute("DROP TYPE IF EXISTS workflow_mode")
    op.execute("DROP TYPE IF EXISTS extraction_mode")
    op.execute("DROP TYPE IF EXISTS job_state")
    op.execute("BEGIN")
