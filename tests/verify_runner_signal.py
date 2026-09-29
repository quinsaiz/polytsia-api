"""Run on the host to verify parent-only signals stop the isolated test stack."""

import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_DIR / "docker-compose.test.yml"


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True)


def project_for_runner(pid: int) -> str | None:
    pattern = re.compile(rf"^(polytsia-test-\d+-{pid})-tests-1$")
    names = docker(
        "ps",
        "--format",
        "{{.Names}}",
        "--filter",
        "label=com.docker.compose.service=tests",
    ).stdout.splitlines()
    for name in names:
        if match := pattern.match(name):
            return match.group(1)
    return None


def resources_remain(project: str) -> bool:
    containers = docker(
        "ps",
        "-a",
        "--format",
        "{{.Names}}",
        "--filter",
        f"label=com.docker.compose.project={project}",
    ).stdout
    network = subprocess.run(
        ["docker", "network", "inspect", f"{project}_test-only"],
        capture_output=True,
        check=False,
    )
    return bool(containers.strip()) or network.returncode == 0


def verify(signum: signal.Signals) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "runner.log"
        project = None
        with log_path.open("w") as log:
            runner = subprocess.Popen(
                [str(REPO_DIR / "scripts/test.sh")],
                cwd=REPO_DIR,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    project = project_for_runner(runner.pid)
                    if project and "executing: pytest" in log_path.read_text():
                        break
                    if runner.poll() is not None:
                        raise AssertionError("Test runner exited before pytest started")
                    time.sleep(0.5)
                else:
                    raise AssertionError("Test container did not start before timeout")

                runner.send_signal(signum)
                exit_code = runner.wait(timeout=30)
                expected_code = 128 + signum.value
                assert exit_code == expected_code, (exit_code, expected_code)
                assert not resources_remain(project), project
                print(
                    f"{signum.name} to runner PID: exit {exit_code}; "
                    f"containers and network removed ({project})"
                )
            except Exception:
                print(
                    "\n".join(log_path.read_text().splitlines()[-40:]), file=sys.stderr
                )
                raise
            finally:
                if runner.poll() is None:
                    runner.terminate()
                    try:
                        runner.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        runner.kill()
                        runner.wait()
                if project and resources_remain(project):
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "-f",
                            str(COMPOSE_FILE),
                            "-p",
                            project,
                            "down",
                            "--volumes",
                            "--remove-orphans",
                            "--rmi",
                            "local",
                        ],
                        check=False,
                    )


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"TERM", "INT"}:
        raise SystemExit("Usage: python3 tests/verify_runner_signal.py TERM|INT")
    verify(signal.Signals[f"SIG{sys.argv[1]}"])
