"""extraction_runs: non-destructive, run-scoped extraction

Revision ID: 013_extraction_runs
Revises: 012_interaction_profile
Create Date: 2026-06-16

Introduces ``extraction_runs`` so each crawl page, extracted record, and export
belongs to a run. ``projects.current_extraction_run_id`` points at the completed
run the read endpoints surface; a retry writes to a new run and only promotes it
on success, so a failed retry never destroys prior results. Also adds page
fencing (``lease_token``), record idempotency (``record_ordinal`` + unique
constraint), run-scoped page uniqueness, and a partial unique index enforcing at
most one active run per project. Existing data is backfilled into one completed
run per project.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "013_extraction_runs"
down_revision: Union[str, None] = "012_interaction_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. extraction_runs table.
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "spec_id", sa.Integer(),
            sa.ForeignKey("extraction_specs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("spec_hash", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_active_extraction_run_per_project",
        "extraction_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','RUNNING')"),
    )

    # 2. New columns on child tables + projects (nullable for backfill).
    op.add_column("crawl_pages", sa.Column("extraction_run_id", sa.Integer(), nullable=True))
    op.add_column("crawl_pages", sa.Column("lease_token", sa.String(length=64), nullable=True))
    op.create_index("ix_crawl_pages_extraction_run_id", "crawl_pages", ["extraction_run_id"])
    op.create_foreign_key(
        "fk_crawl_pages_run", "crawl_pages", "extraction_runs",
        ["extraction_run_id"], ["id"], ondelete="CASCADE",
    )

    op.add_column("extracted_records", sa.Column("extraction_run_id", sa.Integer(), nullable=True))
    op.add_column("extracted_records", sa.Column("record_ordinal", sa.Integer(), nullable=True))
    op.create_index("ix_extracted_records_extraction_run_id", "extracted_records", ["extraction_run_id"])
    op.create_foreign_key(
        "fk_extracted_records_run", "extracted_records", "extraction_runs",
        ["extraction_run_id"], ["id"], ondelete="CASCADE",
    )

    op.add_column("exports", sa.Column("extraction_run_id", sa.Integer(), nullable=True))
    op.create_index("ix_exports_extraction_run_id", "exports", ["extraction_run_id"])
    op.create_foreign_key(
        "fk_exports_run", "exports", "extraction_runs",
        ["extraction_run_id"], ["id"], ondelete="CASCADE",
    )

    op.add_column("projects", sa.Column("current_extraction_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_projects_current_run", "projects", "extraction_runs",
        ["current_extraction_run_id"], ["id"], ondelete="SET NULL",
    )

    # 3. Backfill: one COMPLETED run per project that has any prior data.
    op.execute(
        """
        INSERT INTO extraction_runs
            (project_id, spec_id, state, started_at, finished_at,
             total_pages, total_records, created_at, updated_at)
        SELECT p.id,
               (SELECT s.id FROM extraction_specs s
                 WHERE s.project_id = p.id ORDER BY s.created_at DESC LIMIT 1),
               'COMPLETED', now(), now(),
               (SELECT count(*) FROM crawl_pages cp WHERE cp.project_id = p.id),
               (SELECT count(*) FROM extracted_records er WHERE er.project_id = p.id),
               now(), now()
        FROM projects p
        WHERE EXISTS (SELECT 1 FROM crawl_pages cp WHERE cp.project_id = p.id)
           OR EXISTS (SELECT 1 FROM extracted_records er WHERE er.project_id = p.id)
           OR EXISTS (SELECT 1 FROM exports e WHERE e.project_id = p.id)
        """
    )
    op.execute(
        "UPDATE crawl_pages cp SET extraction_run_id = r.id FROM extraction_runs r "
        "WHERE r.project_id = cp.project_id AND cp.extraction_run_id IS NULL"
    )
    op.execute(
        "UPDATE extracted_records er SET extraction_run_id = r.id FROM extraction_runs r "
        "WHERE r.project_id = er.project_id AND er.extraction_run_id IS NULL"
    )
    op.execute(
        "UPDATE exports e SET extraction_run_id = r.id FROM extraction_runs r "
        "WHERE r.project_id = e.project_id AND e.extraction_run_id IS NULL"
    )
    op.execute(
        "UPDATE projects p SET current_extraction_run_id = r.id FROM extraction_runs r "
        "WHERE r.project_id = p.id"
    )

    # 4. Swap page uniqueness from project-scoped to run-scoped.
    op.drop_constraint("uq_crawl_pages_project_url", "crawl_pages", type_="unique")
    op.create_unique_constraint(
        "uq_crawl_pages_run_url", "crawl_pages", ["extraction_run_id", "normalized_url"]
    )

    # 5. Record idempotency constraint.
    op.create_unique_constraint(
        "uq_extracted_records_run_page_ordinal",
        "extracted_records",
        ["extraction_run_id", "page_id", "record_ordinal"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_extracted_records_run_page_ordinal", "extracted_records", type_="unique"
    )
    op.drop_constraint("uq_crawl_pages_run_url", "crawl_pages", type_="unique")
    op.create_unique_constraint(
        "uq_crawl_pages_project_url", "crawl_pages", ["project_id", "normalized_url"]
    )

    op.drop_constraint("fk_projects_current_run", "projects", type_="foreignkey")
    op.drop_column("projects", "current_extraction_run_id")

    op.drop_constraint("fk_exports_run", "exports", type_="foreignkey")
    op.drop_index("ix_exports_extraction_run_id", table_name="exports")
    op.drop_column("exports", "extraction_run_id")

    op.drop_constraint("fk_extracted_records_run", "extracted_records", type_="foreignkey")
    op.drop_index("ix_extracted_records_extraction_run_id", table_name="extracted_records")
    op.drop_column("extracted_records", "record_ordinal")
    op.drop_column("extracted_records", "extraction_run_id")

    op.drop_constraint("fk_crawl_pages_run", "crawl_pages", type_="foreignkey")
    op.drop_index("ix_crawl_pages_extraction_run_id", table_name="crawl_pages")
    op.drop_column("crawl_pages", "lease_token")
    op.drop_column("crawl_pages", "extraction_run_id")

    op.drop_index("uq_active_extraction_run_per_project", table_name="extraction_runs")
    op.drop_table("extraction_runs")
