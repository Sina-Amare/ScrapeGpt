"""project based extraction workflow

Revision ID: 007
Revises: 006
Create Date: 2026-06-07

Moves the Phase 1 analysis object from jobs to projects and adds the durable
selection, preview, extraction, and export foundation. The PostgreSQL enum keeps
its historical name (job_state) to avoid a risky enum type rename during this
product migration.
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROJECT_STATE_VALUES = (
    "PREVIEWING",
    "PREVIEW_READY",
    "DISCOVERING",
    "EXTRACTING",
    "EXPORTING",
    "COMPLETED",
    "PAUSED",
)


def upgrade() -> None:
    # ADD VALUE cannot run inside a transaction block (PostgreSQL restriction).
    with op.get_context().autocommit_block():
        for value in PROJECT_STATE_VALUES:
            op.execute(f"ALTER TYPE job_state ADD VALUE IF NOT EXISTS '{value}'")

    op.rename_table("jobs", "projects")
    op.execute("ALTER INDEX IF EXISTS ix_jobs_user_id_created_at RENAME TO ix_projects_user_id_created_at")
    op.execute("ALTER INDEX IF EXISTS ix_jobs_user_id_state RENAME TO ix_projects_user_id_state")
    op.execute("ALTER INDEX IF EXISTS ix_jobs_state_updated_at RENAME TO ix_projects_state_updated_at")

    op.execute("CREATE TYPE crawl_page_state AS ENUM ('PENDING','FETCHING','FETCHED','EXTRACTED','BLOCKED','FAILED')")

    op.create_table(
        "extraction_specs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            postgresql.ENUM("STRUCTURED", "CONTENT", name="extraction_mode", create_type=False),
            nullable=False,
        ),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("content_config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("url_patterns", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("page_limit", sa.Integer(), server_default="500", nullable=False),
        sa.Column("export_format", sa.String(length=16), server_default="csv", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_specs_project_id", "extraction_specs", ["project_id"])

    op.create_table(
        "preview_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("spec_id", sa.Integer(), nullable=False),
        sa.Column("sample_records", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("missing_fields", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("quality_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spec_id"], ["extraction_specs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_preview_results_project_id", "preview_results", ["project_id"])
    op.create_index("ix_preview_results_spec_id", "preview_results", ["spec_id"])

    op.create_table(
        "crawl_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("normalized_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "state",
            postgresql.ENUM("PENDING", "FETCHING", "FETCHED", "EXTRACTED", "BLOCKED", "FAILED", name="crawl_page_state", create_type=False),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("depth", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("block_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "normalized_url", name="uq_crawl_pages_project_url"),
    )
    op.create_index("ix_crawl_pages_project_state", "crawl_pages", ["project_id", "state"])
    op.create_index("ix_crawl_pages_state_lease", "crawl_pages", ["state", "lease_expires_at"])

    op.create_table(
        "extracted_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["crawl_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extracted_records_project_id", "extracted_records", ["project_id"])
    op.create_index("ix_extracted_records_page_id", "extracted_records", ["page_id"])

    op.create_table(
        "exports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=2048), nullable=True),
        sa.Column("record_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exports_project_id", "exports", ["project_id"])

    _backfill_specs()


def _backfill_specs() -> None:
    if context.is_offline_mode():
        # Offline SQL generation cannot inspect existing JSON rows in Python.
        # Emit a structural fallback so generated SQL remains valid; online
        # migrations use the richer Python backfill below.
        op.execute(
            """
            INSERT INTO extraction_specs
                (project_id, mode, fields, content_config, url_patterns, page_limit, export_format)
            SELECT id, extraction_mode, '[]'::jsonb, '{}'::jsonb, '[]'::jsonb, 500, 'csv'
            FROM projects
            """
        )
        return

    connection = op.get_bind()
    projects = connection.execute(
        sa.text("SELECT id, extraction_mode, analysis FROM projects")
    ).mappings()

    for project in projects:
        mode = project["extraction_mode"]
        analysis = project["analysis"] or {}
        fields: list[dict] = []
        content_config: dict = {}

        if mode == "STRUCTURED":
            for field in analysis.get("candidate_fields", []) or []:
                confidence = float(field.get("confidence") or 0)
                fields.append(
                    {
                        "name": field.get("name"),
                        "label": field.get("label") or field.get("name"),
                        "user_label": field.get("label") or field.get("name"),
                        "selector": field.get("selector"),
                        "type": field.get("data_type") or field.get("type") or "string",
                        "selected": confidence >= 0.7,
                        "required": bool(field.get("required")),
                        "confidence": confidence,
                        "sample_values": field.get("sample_values") or [],
                        "warnings": [],
                    }
                )
        else:
            content_config = {
                "primary_selector": analysis.get("primary_content_selector"),
                "recommended_chunking": analysis.get("recommended_chunking"),
                "content_type": analysis.get("content_type"),
                "metadata_fields": analysis.get("metadata_fields") or [],
            }

        connection.execute(
            sa.text(
                """
                INSERT INTO extraction_specs
                    (project_id, mode, fields, content_config, url_patterns, page_limit, export_format)
                VALUES
                    (:project_id, :mode, CAST(:fields AS jsonb), CAST(:content_config AS jsonb),
                     '[]'::jsonb, 500, 'csv')
                """
            ),
            {
                "project_id": project["id"],
                "mode": mode,
                "fields": json.dumps(fields),
                "content_config": json.dumps(content_config),
            },
        )


def downgrade() -> None:
    op.drop_index("ix_exports_project_id", table_name="exports")
    op.drop_table("exports")
    op.drop_index("ix_extracted_records_page_id", table_name="extracted_records")
    op.drop_index("ix_extracted_records_project_id", table_name="extracted_records")
    op.drop_table("extracted_records")
    op.drop_index("ix_crawl_pages_state_lease", table_name="crawl_pages")
    op.drop_index("ix_crawl_pages_project_state", table_name="crawl_pages")
    op.drop_table("crawl_pages")
    op.drop_index("ix_preview_results_spec_id", table_name="preview_results")
    op.drop_index("ix_preview_results_project_id", table_name="preview_results")
    op.drop_table("preview_results")
    op.drop_index("ix_extraction_specs_project_id", table_name="extraction_specs")
    op.drop_table("extraction_specs")
    op.execute("DROP TYPE IF EXISTS crawl_page_state")

    op.execute("ALTER INDEX IF EXISTS ix_projects_user_id_created_at RENAME TO ix_jobs_user_id_created_at")
    op.execute("ALTER INDEX IF EXISTS ix_projects_user_id_state RENAME TO ix_jobs_user_id_state")
    op.execute("ALTER INDEX IF EXISTS ix_projects_state_updated_at RENAME TO ix_jobs_state_updated_at")
    op.rename_table("projects", "jobs")
    # PostgreSQL enum labels added in upgrade are intentionally left in place.
    # Removing enum labels requires a type rebuild and is not needed for a
    # compatibility downgrade back to the Phase 1 table shape.
