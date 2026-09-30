import runpy
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.conftest import test_engine


def _verify_upgrade(connection: Connection) -> None:
    # A temporary table shadows the application table for this connection.
    connection.exec_driver_sql(
        "CREATE TEMP TABLE tier_list_items ("
        "id uuid PRIMARY KEY, tier_list_id uuid NOT NULL, "
        "tier varchar(1) NOT NULL, position integer NOT NULL)"
    )
    list_id = "00000000-0000-0000-0000-000000000100"
    other_list_id = "00000000-0000-0000-0000-000000000200"
    rows = [
        (1, list_id, "S", -3),
        (2, list_id, "S", 0),
        (3, list_id, "S", 0),
        (4, list_id, "A", 5),
        (5, list_id, "A", 5),
        (6, other_list_id, "S", -1),
    ]
    for number, tier_list_id, tier, position in rows:
        connection.execute(
            text(
                "INSERT INTO tier_list_items VALUES "
                "(:id, :tier_list_id, :tier, :position)"
            ),
            {
                "id": f"00000000-0000-0000-0000-{number:012d}",
                "tier_list_id": tier_list_id,
                "tier": tier,
                "position": position,
            },
        )

    path = (
        Path(__file__).parents[2]
        / "alembic/versions/4d72b815c9a0_dense_tier_list_positions.py"
    )
    migration = runpy.run_path(str(path))
    with Operations.context(MigrationContext.configure(connection)):
        migration["upgrade"]()

    actual = connection.exec_driver_sql(
        "SELECT right(id::text, 12), tier_list_id::text, tier, position "
        "FROM tier_list_items ORDER BY tier_list_id, tier, position"
    ).all()
    assert actual == [
        ("000000000004", list_id, "A", 0),
        ("000000000005", list_id, "A", 1),
        ("000000000001", list_id, "S", 0),
        ("000000000002", list_id, "S", 1),
        ("000000000003", list_id, "S", 2),
        ("000000000006", other_list_id, "S", 0),
    ]
    constraints = (
        connection.exec_driver_sql(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'pg_temp.tier_list_items'::regclass"
        )
        .scalars()
        .all()
    )
    assert "ck_tier_item_position_nonnegative" in constraints
    assert "uq_tierlist_tier_position" in constraints


async def test_migration_normalizes_legacy_positions_without_losing_rows() -> None:
    async with test_engine.connect() as connection:
        await connection.run_sync(_verify_upgrade)
        await connection.rollback()
