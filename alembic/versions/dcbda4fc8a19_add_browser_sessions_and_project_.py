"""add_browser_sessions_and_project_session_fk

Revision ID: dcbda4fc8a19
Revises: 009
Create Date: 2026-06-11 22:29:44.036502

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dcbda4fc8a19"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("cookies_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_browser_sessions_domain"),
        "browser_sessions",
        ["domain"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_sessions_user_id"),
        "browser_sessions",
        ["user_id"],
        unique=False,
    )

    op.add_column(
        "projects",
        sa.Column("browser_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_browser_session_id",
        "projects",
        "browser_sessions",
        ["browser_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_projects_browser_session_id", "projects", type_="foreignkey"
    )
    op.drop_column("projects", "browser_session_id")
    op.drop_index(
        op.f("ix_browser_sessions_user_id"), table_name="browser_sessions"
    )
    op.drop_index(
        op.f("ix_browser_sessions_domain"), table_name="browser_sessions"
    )
    op.drop_table("browser_sessions")
