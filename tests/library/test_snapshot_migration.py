import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from tests.conftest import test_engine


async def _alembic(database, *args):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *args,
        env={**os.environ, "POSTGRES_DB": database},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()


async def _verify_cli_skips_completed(database):
    for media in ("movies", "games"):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.backfill_catalog_snapshots",
            "--media",
            media,
            "--batch-size",
            "1",
            "--max-requests",
            "1",
            env={
                **os.environ,
                "POSTGRES_DB": database,
                "DEBUG": "true",
                "REDIS_HOST": "unused.invalid",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, errors = await asyncio.wait_for(process.communicate(), timeout=20)
        assert process.returncode == 0, errors.decode()
        summary = json.loads(output)
        assert summary["complete_scan"] is True
        assert (
            summary["filled"],
            summary["skipped"],
            summary["failed"],
            summary["requests"],
        ) == (0, 1, 0, 0)
        assert "Legacy notes" not in output.decode() + errors.decode()


async def test_real_upgrade_preserves_old_library_records():
    database = f"snapshot_migration_{uuid.uuid4().hex}"
    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    legacy = create_async_engine(
        make_url(settings.test_database_url).set(database=database), poolclass=NullPool
    )
    try:
        await _alembic(database, "upgrade", "4d72b815c9a0")
        owner = uuid.uuid4()
        async with legacy.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id,email,username,hashed_password,is_active,is_verified) "
                    "VALUES (:id,'legacy@example.com','legacy','unused',true,false)"
                ),
                {"id": owner},
            )
            for table, id_field in (
                ("user_movies", "tmdb_id"),
                ("user_games", "rawg_id"),
            ):
                await connection.execute(
                    text(
                        f"INSERT INTO {table} (id,user_id,{id_field},status,personal_rating,tier,notes,external_rating) "
                        "VALUES (:id,:owner,155,'completed',9,'S','Legacy notes',4.5)"
                    ),
                    {"id": uuid.uuid4(), "owner": owner},
                )
            before = {
                table: (await connection.execute(text(f"SELECT * FROM {table}")))
                .mappings()
                .one()
                for table in ("user_movies", "user_games")
            }
            assert "catalog_title" not in before["user_movies"]
        await _alembic(database, "upgrade", "head")
        async with legacy.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "c82b7e149f30"
            )
            for table, old in before.items():
                current = (
                    (await connection.execute(text(f"SELECT * FROM {table}")))
                    .mappings()
                    .one()
                )
                assert {key: current[key] for key in old} == dict(old)
                added = set(current) - set(old)
                assert added == {
                    "catalog_title",
                    "catalog_release_date",
                    "catalog_metadata_fetched_at",
                    "catalog_poster_path"
                    if table == "user_movies"
                    else "catalog_background_image",
                }
                assert all(current[key] is None for key in added)
        await _alembic(database, "downgrade", "4d72b815c9a0")
        async with legacy.connect() as connection:
            for table, old in before.items():
                assert dict(
                    (await connection.execute(text(f"SELECT * FROM {table}")))
                    .mappings()
                    .one()
                ) == dict(old)
        await _alembic(database, "upgrade", "head")
        async with legacy.begin() as connection:
            for table in before:
                await connection.execute(
                    text(
                        f"UPDATE {table} SET catalog_title='Captured', catalog_metadata_fetched_at=now()"
                    )
                )
        await _verify_cli_skips_completed(database)
    finally:
        await legacy.dispose()
        async with test_engine.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            await connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
