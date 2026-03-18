import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_webui.routers.runtime import _compute_compatibility


SCRIPT_PATH = Path("scripts/run_llama.sh").resolve()


def _make_fake_llama_server(bin_dir: Path) -> Path:
    llama_path = bin_dir / "llama-server"
    llama_path.write_text(
        "#!/usr/bin/env bash\n"
        "trap 'exit 0' TERM INT\n"
        "sleep 60\n"
    )
    llama_path.chmod(0o755)
    return llama_path


def _make_fake_flock(bin_dir: Path) -> Path:
    flock_path = bin_dir / "flock"
    flock_path.write_text("#!/usr/bin/env bash\nexit 0\n")
    flock_path.chmod(0o755)
    return flock_path


def _make_runtime_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    runtime_dir = tmp_path / "runtime"
    models_dir = tmp_path / "models"
    bin_dir.mkdir()
    runtime_dir.mkdir()
    models_dir.mkdir()
    _make_fake_llama_server(bin_dir)
    _make_fake_flock(bin_dir)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["RUNTIME_DIR"] = str(runtime_dir)
    env["MODELS_DIR"] = str(models_dir)
    env["THREADS"] = "1"
    env["THREADS_BATCH"] = "1"
    env["DUAL_PROFILE_MODELS_DIR"] = str(models_dir)
    env["BEAST_PROFILE_MODELS_DIR"] = str(models_dir)
    env["DUAL_PROFILE_EXTRA_ARGS"] = "--parallel 1 --metrics"
    env["BEAST_PROFILE_EXTRA_ARGS"] = "--parallel 1 --metrics"
    return env


def _run_script(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_run_llama_status_json_is_valid_when_stopped(tmp_path):
    env = _make_runtime_env(tmp_path)

    result = _run_script("status", "--json", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["state_version"] == "1"
    assert payload["running"] is False
    assert payload["profile"] == "manual"
    assert payload["resolved_params"]["ctx"] == 131072


def test_run_llama_status_json_reports_stale_pid(tmp_path):
    env = _make_runtime_env(tmp_path)
    runtime_dir = Path(env["RUNTIME_DIR"])
    (runtime_dir / "llama-server.pid").write_text("999999")

    result = _run_script("status", "--json", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["running"] is False
    assert payload["error"] == "stale pid"


def test_run_llama_logs_lines_returns_recent_lines(tmp_path):
    env = _make_runtime_env(tmp_path)
    runtime_dir = Path(env["RUNTIME_DIR"])
    (runtime_dir / "llama-server.log").write_text("1\n2\n3\n4\n5\n")

    result = _run_script("logs", "--lines", "2", env=env)

    assert result.returncode == 0
    assert result.stdout.strip().splitlines() == ["4", "5"]


def test_restart_profile_dual_reports_models_max_two(tmp_path):
    env = _make_runtime_env(tmp_path)

    try:
        start_result = _run_script("restart-profile", "dual", env=env)
        assert start_result.returncode == 0

        status_result = _run_script("status", "--json", env=env)
        payload = json.loads(status_result.stdout)
        assert payload["profile"] == "dual"
        assert payload["resolved_params"]["models_max"] == 2
        assert payload["running"] is True
    finally:
        _run_script("stop", env=env)


def test_restart_profile_beast_reports_models_max_one(tmp_path):
    env = _make_runtime_env(tmp_path)

    try:
        start_result = _run_script("restart-profile", "beast", env=env)
        assert start_result.returncode == 0

        status_result = _run_script("status", "--json", env=env)
        payload = json.loads(status_result.stdout)
        assert payload["profile"] == "beast"
        assert payload["resolved_params"]["models_max"] == 1
        assert payload["running"] is True
    finally:
        _run_script("stop", env=env)


def test_runtime_compatibility_warns_for_beast_with_specialist_enabled():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL="specialist-local",
                    TASK_MODEL_EXTERNAL="specialist-external",
                    ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER=True,
                )
            )
        )
    )

    compatibility = _compute_compatibility(request, "beast")

    assert compatibility.profile_compatibility == "warning"
    assert len(compatibility.issues) == 2


def test_runtime_compatibility_warns_for_dual_without_specialist():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                    ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER=False,
                )
            )
        )
    )

    compatibility = _compute_compatibility(request, "dual")

    assert compatibility.profile_compatibility == "warning"
    assert len(compatibility.issues) == 2


def test_runtime_compatibility_is_ok_for_matching_dual_setup():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL="specialist-local",
                    TASK_MODEL_EXTERNAL="specialist-external",
                    ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER=True,
                )
            )
        )
    )

    compatibility = _compute_compatibility(request, "dual")

    assert compatibility.profile_compatibility == "ok"
    assert compatibility.issues == []
