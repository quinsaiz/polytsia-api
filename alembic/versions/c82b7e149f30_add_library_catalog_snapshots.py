"""Add nullable catalog snapshots to personal libraries.

Revision ID: c82b7e149f30
Revises: 4d72b815c9a0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c82b7e149f30"
down_revision: str | Sequence[str] | None = "4d72b815c9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, image in (
        ("user_movies", "catalog_poster_path"),
        ("user_games", "catalog_background_image"),
    ):
        op.add_column(table, sa.Column("catalog_title", sa.String(), nullable=True))
        op.add_column(table, sa.Column(image, sa.String(), nullable=True))
        op.add_column(table, sa.Column("catalog_release_date", sa.Date(), nullable=True))
        op.add_column(
            table,
            sa.Column("catalog_metadata_fetched_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table, image in (
        ("user_games", "catalog_background_image"),
        ("user_movies", "catalog_poster_path"),
    ):
        for column in (
            "catalog_metadata_fetched_at", "catalog_release_date", image, "catalog_title"
        ):
            op.drop_column(table, column)
