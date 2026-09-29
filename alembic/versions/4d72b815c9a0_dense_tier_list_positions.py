"""Normalize and constrain tier list positions.

Revision ID: 4d72b815c9a0
Revises: 8730b4df3e2e
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4d72b815c9a0"
down_revision: str | Sequence[str] | None = "8730b4df3e2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Preserve every row; old position and UUID provide a stable tie break.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY tier_list_id, tier ORDER BY position, id
            ) - 1 AS new_position
            FROM tier_list_items
        )
        UPDATE tier_list_items AS item
        SET position = ranked.new_position
        FROM ranked
        WHERE item.id = ranked.id
        """
    )
    op.create_check_constraint(
        "ck_tier_item_position_nonnegative", "tier_list_items", "position >= 0"
    )
    op.create_unique_constraint(
        "uq_tierlist_tier_position",
        "tier_list_items",
        ["tier_list_id", "tier", "position"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("uq_tierlist_tier_position", "tier_list_items", type_="unique")
    op.drop_constraint(
        "ck_tier_item_position_nonnegative", "tier_list_items", type_="check"
    )
