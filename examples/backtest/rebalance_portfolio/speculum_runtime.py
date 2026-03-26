from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent


def discover_workspace_root() -> Path:
    return WORKSPACE_ROOT


def discover_stone_root() -> Path:
    return discover_workspace_root() / "philosophers-stone"


def discover_speculum_backend_root() -> Path:
    return discover_workspace_root() / "speculum" / "backend"


def _discover_backend_python() -> Path:
    backend_root = discover_speculum_backend_root()
    venv_python = backend_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path("python")


def _docker_container_exists(name: str) -> bool:
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    return name in names


def _parse_json_payload(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if not line.startswith("\x1b")]
    json_start = next(index for index, line in enumerate(lines) if line.lstrip().startswith("{"))
    return json.loads("\n".join(lines[json_start:]))


def run_backend_python(script: str) -> dict:
    container_name = os.getenv("SPECULUM_BACKEND_CONTAINER", "speculum-backend")
    if _docker_container_exists(container_name):
        command = [
            "docker",
            "exec",
            container_name,
            "sh",
            "-lc",
            f"cd /app && python - <<'PY'\n{script}\nPY",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        return _parse_json_payload(completed.stdout)

    backend_root = discover_speculum_backend_root()
    env = os.environ.copy()
    env.setdefault("DATABASE_HOST", "localhost")
    env.setdefault("DATABASE_PORT", "5431")
    env.setdefault("DATABASE_USER", "speculum")
    env.setdefault("DATABASE_NAME", "speculum")
    env.setdefault("DATABASE_PASSWORD", "speculum_dev_CHANGE_ME")
    env.setdefault("SECURITY_PASSWORD", "admin123")
    env.setdefault("PHILOSOPHERS_STONE_PATH", str(discover_stone_root()))
    env.setdefault("BACKTEST_DATA_DIRECTORY", "/tmp/speculum_rebalancing_data")

    command = [
        str(_discover_backend_python()),
        "-c",
        script,
    ]
    completed = subprocess.run(
        command,
        cwd=backend_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return _parse_json_payload(completed.stdout)
