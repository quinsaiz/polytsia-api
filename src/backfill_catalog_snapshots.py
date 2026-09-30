"""Explicit, bounded, single-process library snapshot backfill.

Run with python -m src.backfill_catalog_snapshots --help.
"""

import argparse
import asyncio
import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass

import httpx
from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.catalog_snapshot import catalog_snapshot
from src.database import AsyncSessionFactory, engine
from src.games.models import UserGame
from src.games.service import get_game_details
from src.movies.models import UserMovie
from src.movies.service import get_movie_details

logger = logging.getLogger(__name__)


@dataclass
class BackfillSummary:
    filled: int = 0
    skipped: int = 0
    failed: int = 0
    not_found: int = 0
    unavailable: int = 0
    database_errors: int = 0
    requests: int = 0
    next_after_id: str | None = None
    complete_scan: bool = False


def _validate_options(
    media: str, batch_size: int, max_requests: int, delay: float
) -> None:
    if media not in {"movies", "games"}:
        raise ValueError("media must be movies or games")
    if not 1 <= batch_size <= 1000 or max_requests < 1:
        raise ValueError("batch_size must be 1..1000 and max_requests positive")
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("delay must be finite and nonnegative")


async def backfill(
    media: str,
    sessions: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    *,
    batch_size: int = 100,
    max_requests: int = 1000,
    delay: float = 1.0,
    after_id: uuid.UUID | None = None,
    summary: BackfillSummary | None = None,
) -> BackfillSummary:
    _validate_options(media, batch_size, max_requests, delay)
    summary = summary if summary is not None else BackfillSummary()
    summary.next_after_id = str(after_id) if after_id else None
    model = UserMovie if media == "movies" else UserGame
    catalog_id = UserMovie.tmdb_id if media == "movies" else UserGame.rawg_id
    pending = or_(
        model.catalog_metadata_fetched_at.is_(None), model.catalog_title.is_(None)
    )
    # Fix an upper UUID boundary: new tracking is not the responsibility of this
    # run. Keyset traversal visits failed/deleted rows at most once per run.
    async with sessions() as db:
        upper = await db.scalar(select(model.id).order_by(model.id.desc()).limit(1))
    if upper is None:
        summary.complete_scan = True
        return summary

    while True:
        stmt = (
            select(model.id, catalog_id, pending.label("pending"))
            .where(model.id <= upper)
            .order_by(model.id)
            .limit(batch_size)
        )
        if after_id is not None:
            stmt = stmt.where(model.id > after_id)
        async with sessions() as db:
            rows = (await db.execute(stmt)).all()
        if not rows:
            summary.complete_scan = True
            return summary
        for record_id, external_id, needs_snapshot in rows:
            if needs_snapshot:
                if summary.requests >= max_requests:
                    return summary
                if summary.requests:
                    await asyncio.sleep(delay)
                summary.requests += 1
                await _fill_record(
                    media, record_id, external_id, sessions, http_client, summary
                )
                if summary.database_errors:
                    return summary
            else:
                summary.skipped += 1
            after_id = record_id
            # Continue scanning past catalog failures, but never suggest a resume
            # cursor that would omit an unconfirmed write or failed fetch.
            summary.next_after_id = (
                summary.next_after_id if summary.failed else str(record_id)
            )


async def _fill_record(
    media: str,
    record_id: uuid.UUID,
    external_id: int,
    sessions: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    summary: BackfillSummary,
) -> None:
    try:
        details = (
            await get_movie_details(external_id, http_client, use_cache=False)
            if media == "movies"
            else await get_game_details(external_id, http_client, use_cache=False)
        )
    except HTTPException as error:
        summary.failed += 1
        if error.status_code == 404:
            summary.not_found += 1
        else:
            summary.unavailable += 1
        logger.warning(
            "Snapshot fetch failed: media=%s record=%s status=%s",
            media,
            record_id,
            error.status_code,
        )
        return

    model = UserMovie if media == "movies" else UserGame
    try:
        async with sessions() as db, db.begin():
            saved = await db.scalar(
                update(model)
                .where(
                    model.id == record_id,
                    or_(
                        model.catalog_metadata_fetched_at.is_(None),
                        model.catalog_title.is_(None),
                    ),
                )
                # Preserve personal updated_at, including concurrent PATCHes.
                .values(**catalog_snapshot(details), updated_at=model.updated_at)
                .returning(model.id)
            )
        if saved is None:
            summary.skipped += 1  # Deleted or completed since the batch was read.
        else:
            summary.filled += 1
    except (SQLAlchemyError, OSError):
        # asyncpg connection failures can escape SQLAlchemy without wrapping.
        summary.failed += 1
        summary.database_errors += 1
        logger.error("Snapshot write failed: media=%s record=%s", media, record_id)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", required=True, choices=("movies", "games"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Seconds between requests"
    )
    parser.add_argument(
        "--after-id", type=uuid.UUID, help="Resume after a reported UUID"
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000 or args.max_requests < 1:
        parser.error("batch-size must be 1..1000 and max-requests positive")
    if not math.isfinite(args.delay) or args.delay < 0:
        parser.error("delay must be finite and nonnegative")
    return args


async def _run(args: argparse.Namespace) -> int:
    summary = BackfillSummary()
    # Do not emit SQL parameters, HTTP URLs (RAWG key), or payloads in this CLI,
    # even if the application's DEBUG setting enables engine echo.
    engine.echo = False
    for name in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await backfill(
                args.media,
                AsyncSessionFactory,
                client,
                batch_size=args.batch_size,
                max_requests=args.max_requests,
                delay=args.delay,
                after_id=args.after_id,
                summary=summary,
            )
    except (SQLAlchemyError, OSError):
        summary.failed += 1
        summary.database_errors += 1
        logger.error("Backfill stopped: database scan failed")
    finally:
        await engine.dispose()
    print(json.dumps(asdict(summary), sort_keys=True))
    if summary.failed:
        return 1
    return 0 if summary.complete_scan else 2


def main() -> None:
    args = _arguments()
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
