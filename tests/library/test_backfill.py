import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.backfill_catalog_snapshots import backfill
from src.config import settings
from src.games.models import UserGame
from src.movies.models import UserMovie
from tests.library.test_cards import payload


@pytest.mark.parametrize(
    "media,model,id_field",
    [("movies", UserMovie, "tmdb_id"), ("games", UserGame, "rawg_id")],
)
async def test_backfill_batches_failure_resume_and_idempotence(
    media, model, id_field, db, owner, monkeypatch, caplog
):
    for name in ("cache_get", "cache_set"):
        monkeypatch.setattr(
            f"src.{media}.service.{name}",
            AsyncMock(side_effect=AssertionError("Redis used")),
        )
    monkeypatch.setattr(settings, "rawg_api_key", "secret-backfill-key")
    rows = []
    for number in range(1, 10):
        row = model(
            id=uuid.UUID(int=number),
            user_id=owner.id,
            **{id_field: number},
            status="completed",
            personal_rating=9,
            tier="S",
            notes="sensitive-notes",
            external_rating=3.5,
        )
        rows.append(row)
    rows[0].catalog_title = "Already saved"
    rows[0].catalog_metadata_fetched_at = datetime.now(UTC)
    rows[
        1
    ].catalog_title = "Partial snapshot"  # No fetched marker: replace the snapshot.
    db.add_all(rows)
    await db.commit()
    stamps = [(r.created_at, r.updated_at) for r in rows]
    sessions = async_sessionmaker(
        bind=db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    calls = []
    failures = {3: 404, 4: 429, 5: 500, 6: 503, 7: "timeout", 8: "malformed"}

    def handler(request):
        number = int(request.url.path.rsplit("/", 1)[-1])
        calls.append(number)
        failure = failures.get(number)
        if failure == "timeout":
            raise httpx.ReadTimeout(
                "sensitive-notes secret-backfill-key", request=request
            )
        if failure == "malformed":
            return httpx.Response(200, json={"secret": "secret-backfill-key"})
        if failure:
            return httpx.Response(failure)
        return httpx.Response(200, json=payload(media, number))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        first = await backfill(
            media, sessions, upstream, batch_size=2, max_requests=2, delay=0
        )
        assert (first.filled, first.skipped, first.failed, first.not_found) == (
            1,
            1,
            1,
            1,
        )
        assert not first.complete_scan
        assert first.next_after_id == str(rows[1].id)
        second = await backfill(
            media,
            sessions,
            upstream,
            batch_size=2,
            delay=0,
            after_id=uuid.UUID(first.next_after_id),
        )
        assert second.complete_scan
        assert (second.filled, second.failed, second.unavailable) == (1, 6, 5)
        assert second.next_after_id == str(rows[1].id)
        assert calls == [2, 3, *range(3, 10)]  # Later good rows still succeed.
        failures.clear()
        calls.clear()
        retry = await backfill(media, sessions, upstream, batch_size=1, delay=0)
        assert (retry.filled, retry.skipped, retry.failed) == (6, 3, 0)
        assert calls == list(range(3, 9))
        calls.clear()
        again = await backfill(media, sessions, upstream, batch_size=2, delay=0)
        assert (again.filled, again.skipped, again.failed) == (0, 9, 0)
        assert calls == []  # Legitimate null dates/images are complete snapshots.
    for index, row in enumerate(rows):
        await db.refresh(row)
        assert (
            row.status,
            row.personal_rating,
            row.tier,
            row.notes,
            row.external_rating,
        ) == ("completed", 9, "S", "sensitive-notes", 3.5)
        assert (row.created_at, row.updated_at) == stamps[index]
        assert row.catalog_metadata_fetched_at is not None
        assert row.catalog_release_date is None
    assert rows[0].catalog_title == "Already saved"
    assert "secret-backfill-key" not in caplog.text
    assert "sensitive-notes" not in caplog.text


@pytest.mark.parametrize(
    "media,model,id_field",
    [("movies", UserMovie, "tmdb_id"), ("games", UserGame, "rawg_id")],
)
@pytest.mark.parametrize("action", ["delete", "patch", "complete"])
async def test_record_changes_while_catalog_is_pending(
    media, model, id_field, action, db, owner
):
    row = model(
        user_id=owner.id, **{id_field: 155}, status="planned", external_rating=2.0
    )
    db.add(row)
    await db.commit()
    row_id = row.id
    sessions = async_sessionmaker(
        bind=db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def handler(request):
        async with sessions() as writer, writer.begin():
            if action == "delete":
                await writer.execute(delete(model).where(model.id == row_id))
            else:
                changes = (
                    {
                        "personal_rating": 10,
                        "notes": "Edited during fetch",
                        "tier": "S",
                        "status": "completed",
                    }
                    if action == "patch"
                    else {
                        "catalog_title": "Already completed",
                        "catalog_metadata_fetched_at": datetime.now(UTC),
                    }
                )
                await writer.execute(
                    update(model).where(model.id == row_id).values(**changes)
                )
        return httpx.Response(200, json=payload(media))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        result = await backfill(media, sessions, upstream, delay=0)
    assert result.failed == 0
    if action == "delete":
        assert (result.filled, result.skipped) == (0, 1)
    else:
        await db.refresh(row)
        assert row.external_rating == 2.0
        if action == "patch":
            assert result.filled == 1
            assert (row.personal_rating, row.notes, row.tier, row.status) == (
                10,
                "Edited during fetch",
                "S",
                "completed",
            )
        else:
            assert result.skipped == 1
            assert row.catalog_title == "Already completed"


@pytest.mark.parametrize(
    "failure_at,expected_filled", [(1, 0), (2, 0), (3, 0), (4, 1), (5, 1)]
)
@pytest.mark.parametrize(
    "error_type", [ConnectionRefusedError, TimeoutError, SQLAlchemyError]
)
async def test_cli_connection_failure_reports_safe_resume_cursor(
    failure_at, expected_filled, error_type, db, owner, monkeypatch, capsys, caplog
):
    """Exercise the CLI result with real sessions and a failing asyncpg creator."""
    import argparse
    import json

    from sqlalchemy.ext.asyncio import create_async_engine

    from src import backfill_catalog_snapshots as cli

    start = uuid.UUID(int=100)
    rows = [
        UserMovie(
            id=uuid.UUID(int=number),
            user_id=owner.id,
            tmdb_id=number,
            status="completed",
            notes="personal-notes",
            personal_rating=9,
        )
        for number in (101, 102, 103)
    ]
    db.add_all(rows)
    await db.commit()
    sessions = async_sessionmaker(
        bind=db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    secret = "postgresql://private-user:private-password@private-host/private-db"

    async def refuse_connection():
        raise error_type(secret)

    failing_engine = create_async_engine(
        "postgresql+asyncpg://", async_creator=refuse_connection
    )
    failing_sessions = async_sessionmaker(failing_engine)
    opened = 0

    def session_factory():
        nonlocal opened
        opened += 1
        return failing_sessions() if opened == failure_at else sessions()

    args = argparse.Namespace(
        media="movies", batch_size=1, max_requests=10, delay=0, after_id=start
    )
    monkeypatch.setattr(cli, "AsyncSessionFactory", session_factory)
    monkeypatch.setattr(cli, "engine", failing_engine)
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=payload("movies", int(request.url.path.rsplit("/", 1)[-1]))
            )
        )
    )
    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda **kwargs: upstream)
    try:
        exit_code = await cli._run(args)
        output = capsys.readouterr()
        summary = json.loads(output.out)
        assert exit_code == 1
        assert summary == {
            "filled": expected_filled,
            "skipped": 0,
            "failed": 1,
            "not_found": 0,
            "unavailable": 0,
            "database_errors": 1,
            "requests": {1: 0, 2: 0, 3: 1, 4: 1, 5: 2}[failure_at],
            "next_after_id": str(rows[0].id if expected_filled else start),
            "complete_scan": False,
        }
        assert secret not in output.out + output.err + caplog.text
        assert "personal-notes" not in output.out + output.err + caplog.text
        for row in rows:
            await db.refresh(row)
        assert rows[1].catalog_metadata_fetched_at is None
        assert rows[2].catalog_metadata_fetched_at is None

        # Resume exactly as documented: no failed row can fall behind this cursor.
        args.after_id = uuid.UUID(summary["next_after_id"])
        monkeypatch.setattr(cli, "AsyncSessionFactory", sessions)
        upstream = type(upstream)(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload("movies"))
            )
        )
        assert await cli._run(args) == 0
        retry = json.loads(capsys.readouterr().out)
        assert retry["filled"] == 3 - expected_filled
        for row in rows:
            await db.refresh(row)
            assert row.catalog_metadata_fetched_at is not None
            assert (row.notes, row.personal_rating) == ("personal-notes", 9)
    finally:
        await upstream.aclose()
        await failing_engine.dispose()


@pytest.mark.parametrize(
    "error_type", [RuntimeError, asyncio.CancelledError, SystemExit]
)
async def test_cli_does_not_swallow_programming_errors_or_cancellation(
    error_type, monkeypatch, capsys
):
    import argparse

    from sqlalchemy.ext.asyncio import create_async_engine

    from src import backfill_catalog_snapshots as cli

    async def fail():
        raise error_type("stop")

    failing_engine = create_async_engine("postgresql+asyncpg://", async_creator=fail)
    monkeypatch.setattr(cli, "engine", failing_engine)
    monkeypatch.setattr(cli, "AsyncSessionFactory", async_sessionmaker(failing_engine))
    args = argparse.Namespace(
        media="movies", batch_size=1, max_requests=1, delay=0, after_id=None
    )
    with pytest.raises(error_type):
        await cli._run(args)
    assert capsys.readouterr().out == ""
