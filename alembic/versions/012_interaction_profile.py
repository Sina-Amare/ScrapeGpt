"""interaction_profile: page-variant extraction config on extraction_specs

Revision ID: 012_interaction_profile
Revises: 011_project_events
Create Date: 2026-06-15

Adds the ``interaction_profile`` JSONB column to ``extraction_specs``. It stores
the page-variant configuration (e.g. per-100g vs per-serving, metric vs
imperial). Default ``{}`` means "disabled", so every existing spec keeps its
current single-variant extraction behaviour with no data change.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "012_interaction_profile"
down_revision: Union[str, None] = "011_project_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_specs",
        sa.Column(
            "interaction_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("extraction_specs", "interaction_profile")
