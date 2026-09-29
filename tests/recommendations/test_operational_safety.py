import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from celery.beat import PersistentScheduler
from celery.schedules import crontab

from src.celery_app import celery_app
from src.config import settings
from src.games import service as game_service
from src.games.exceptions import RAWGServiceUnavailableException
from src.recommendations import tasks


@pytest.fixture
def setup_database() -> None:
    """Scheduler and HTTP logging tests need no database."""


@pytest.mark.parametrize(("hour", "minute"), [(3, 0), (3, 15)])
def test_beat_does_not_catch_up_stale_daily_runs(hour: int, minute: int) -> None:
    now = datetime(2026, 9, 29, 14, 29, tzinfo=UTC)
    stale_run = datetime(2026, 8, 9, 10, 31, tzinfo=UTC)
    schedule = crontab(hour=hour, minute=minute, app=celery_app, nowfun=lambda: now)

    assert schedule.is_due(stale_run).is_due is False


@pytest.mark.parametrize(("hour", "minute"), [(3, 0), (3, 15)])
def test_beat_keeps_daily_refresh(hour: int, minute: int) -> None:
    now = datetime(2026, 9, 29, hour, minute, 10, tzinfo=UTC)
    previous_run = datetime(2026, 9, 28, hour, minute, 10, tzinfo=UTC)
    schedule = crontab(hour=hour, minute=minute, app=celery_app, nowfun=lambda: now)

    assert schedule.is_due(previous_run).is_due is True


def test_fresh_beat_state_waits_for_daily_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 9, 29, 14, 29, tzinfo=UTC)
    monkeypatch.setattr(celery_app, "now", lambda: now)
    scheduler = PersistentScheduler(
        app=celery_app, schedule_filename=str(tmp_path / "beat-state")
    )
    try:
        for name in (
            "refresh-movie-candidates-daily",
            "refresh-game-candidates-daily",
        ):
            assert scheduler.schedule[name].is_due()[0] is False
    finally:
        scheduler.close()


@pytest.mark.parametrize("status", [200, 429])
async def test_rawg_detail_request_never_logs_key(
    status: int, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key = "rawg-log-test-sentinel"
    monkeypatch.setattr(settings, "rawg_api_key", key)

    async def empty_cache(_key: str) -> None:
        return None

    async def ignore_cache_set(_key: str, _value: Any, _ttl: int) -> None:
        return None

    monkeypatch.setattr(game_service, "cache_get", empty_cache)
    monkeypatch.setattr(game_service, "cache_set", ignore_cache_set)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == key
        return httpx.Response(
            status, json={"id": 155, "name": "Test game", "rating": 4.0}
        )

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if status == 200:
                assert (await game_service.get_game_details(155, client)).id == 155
            else:
                with pytest.raises(RAWGServiceUnavailableException):
                    await game_service.get_game_details(155, client)

    assert key not in caplog.text
    if status == 429:
        assert "status=429" in caplog.text


def test_rawg_candidate_retry_never_logs_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key = "rawg-task-test-sentinel"
    monkeypatch.setattr(settings, "rawg_api_key", key)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == key
        return httpx.Response(429)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr("src.recommendations.tasks.httpx.AsyncClient", mock_client)
    with caplog.at_level(logging.INFO):
        result = tasks.refresh_game_candidates.apply(throw=False)

    assert result.failed()
    assert isinstance(result.result, httpx.HTTPError)
    assert key not in str(result.result)
    assert key not in caplog.text
