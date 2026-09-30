import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup_database() -> None:
    """Shell argument and Compose dependency checks require no database."""


@pytest.fixture
def commands(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    log = tmp_path / "commands.jsonl"
    stub = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "wait-for-it" and "redis:6379" in sys.argv:
    sys.exit(int(os.environ.get("REDIS_WAIT_EXIT", "0")))
"""
    for name in ("wait-for-it", "alembic", "uvicorn", "celery", "python"):
        path = tmp_path / name
        path.write_text(stub)
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "REDIS_HOST": "redis",
        "REDIS_PORT": "6379",
    }
    env.pop("RELOAD", None)
    return log, env


@pytest.mark.parametrize("reload", ["false", "true", "", None])
def test_backend_starts_without_redis_and_honors_reload(
    reload: str | None, commands: tuple[Path, dict[str, str]]
) -> None:
    log, env = commands
    env["REDIS_WAIT_EXIT"] = "1"
    if reload is not None:
        env["RELOAD"] = reload
    result = subprocess.run(
        ["bash", str(REPO / "entrypoint.sh"), "backend"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call[0] for call in calls] == ["wait-for-it", "alembic", "uvicorn"]
    assert calls[0][1:3] == ["--service", "db:5432"]
    assert calls[1] == ["alembic", "upgrade", "head"]
    assert calls[2] == [
        "uvicorn",
        "src.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ] + (["--reload"] if reload == "true" else [])


@pytest.mark.parametrize(
    "role", ["celery_worker", "celery_beat", "bootstrap_recommendations"]
)
@pytest.mark.parametrize("redis_available", [False, True])
def test_background_roles_still_require_redis(
    role: str, redis_available: bool, commands: tuple[Path, dict[str, str]]
) -> None:
    log, env = commands
    env["REDIS_WAIT_EXIT"] = "0" if redis_available else "1"
    result = subprocess.run(
        ["bash", str(REPO / "entrypoint.sh"), role],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[1][0:3] == ["wait-for-it", "--service", "redis:6379"]
    if redis_available:
        assert result.returncode == 0
        assert calls[2][0] == (
            "python" if role == "bootstrap_recommendations" else "celery"
        )
    else:
        assert result.returncode != 0
        assert len(calls) == 2


def test_compose_backend_has_no_redis_dependency() -> None:
    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    assert services["backend"]["depends_on"] == ["db"]
    assert services["backend"]["environment"]["RELOAD"] == "${RELOAD:-true}"
    for role in ("celery_worker", "celery_beat", "recommendations_bootstrap"):
        assert services[role]["depends_on"]["redis"]["condition"] == "service_healthy"
