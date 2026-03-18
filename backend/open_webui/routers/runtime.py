import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_admin_user

log = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_PROFILES = {"dual", "beast"}
TRANSITION_RUNNING_STATES = {"starting", "stopping"}

_action_lock = asyncio.Lock()
_transition_state = {
    "state": None,
    "profile": None,
    "last_error": None,
}


class RuntimeActionForm(BaseModel):
    profile: Literal["dual", "beast"]


class RuntimeResolvedParams(BaseModel):
    models_max: int = 0
    ctx: int = 0
    batch: int = 0
    ubatch_size: int = 0
    cache_prompt: bool = False
    cache_reuse: int = 0
    cache_k: str = ""
    cache_v: str = ""
    extra_args: list[str] = Field(default_factory=list)


class LauncherStatus(BaseModel):
    state_version: str = "1"
    running: bool = False
    profile: str = "manual"
    pid: str = ""
    server_mode: str = ""
    model_path: str = ""
    models_dir: str = ""
    host: str = ""
    port: int = 0
    log_file: str = ""
    pid_file: str = ""
    model_file: str = ""
    resolved_params: RuntimeResolvedParams = Field(default_factory=RuntimeResolvedParams)
    error: str = ""


class RuntimeCompatibility(BaseModel):
    profile_compatibility: Literal["ok", "warning"] = "ok"
    issues: list[str] = Field(default_factory=list)


class RuntimeStatusResponse(BaseModel):
    state: Literal["stopped", "starting", "running", "stopping", "error"] = "stopped"
    running: bool = False
    profile: str = "manual"
    launcher_status: LauncherStatus = Field(default_factory=LauncherStatus)
    resolved_params: RuntimeResolvedParams = Field(default_factory=RuntimeResolvedParams)
    compatibility: RuntimeCompatibility = Field(default_factory=RuntimeCompatibility)
    last_error: Optional[str] = None
    script_path: str = ""


class RuntimeLogsResponse(BaseModel):
    log_file: str = ""
    lines_requested: int = 200
    lines: list[str] = Field(default_factory=list)
    error: Optional[str] = None


def _script_path(request: Request) -> Path:
    configured = getattr(request.app.state.config, "RUNTIME_CONTROL_SCRIPT_PATH", "") or ""
    return Path(configured).expanduser()


def _empty_launcher_status(*, script_path: Path, error: str = "") -> LauncherStatus:
    return LauncherStatus(
        profile="manual",
        log_file=str(script_path.parent / ".local/state/llama-server/llama-server.log")
        if script_path.parent
        else "",
        error=error,
    )


async def _run_launcher_command(script_path: Path, *args: str) -> tuple[int, str, str]:
    if not script_path.exists():
        raise RuntimeError(f"Runtime control script not found: {script_path}")
    if not os.access(script_path, os.X_OK):
        raise RuntimeError(f"Runtime control script is not executable: {script_path}")

    process = await asyncio.create_subprocess_exec(
        str(script_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def _coerce_launcher_status(payload: dict) -> LauncherStatus:
    resolved_params = payload.get("resolved_params") or {}
    return LauncherStatus(
        state_version=str(payload.get("state_version") or "1"),
        running=bool(payload.get("running")),
        profile=str(payload.get("profile") or "manual"),
        pid=str(payload.get("pid") or ""),
        server_mode=str(payload.get("server_mode") or ""),
        model_path=str(payload.get("model_path") or ""),
        models_dir=str(payload.get("models_dir") or ""),
        host=str(payload.get("host") or ""),
        port=int(payload.get("port") or 0),
        log_file=str(payload.get("log_file") or ""),
        pid_file=str(payload.get("pid_file") or ""),
        model_file=str(payload.get("model_file") or ""),
        resolved_params=RuntimeResolvedParams(
            models_max=int(resolved_params.get("models_max") or 0),
            ctx=int(resolved_params.get("ctx") or 0),
            batch=int(resolved_params.get("batch") or 0),
            ubatch_size=int(resolved_params.get("ubatch_size") or 0),
            cache_prompt=bool(resolved_params.get("cache_prompt")),
            cache_reuse=int(resolved_params.get("cache_reuse") or 0),
            cache_k=str(resolved_params.get("cache_k") or ""),
            cache_v=str(resolved_params.get("cache_v") or ""),
            extra_args=[
                str(value)
                for value in (resolved_params.get("extra_args") or [])
                if value is not None
            ],
        ),
        error=str(payload.get("error") or ""),
    )


async def _get_launcher_status(request: Request) -> LauncherStatus:
    script_path = _script_path(request)
    try:
        returncode, stdout, stderr = await _run_launcher_command(script_path, "status", "--json")
        if returncode != 0:
            message = stderr or stdout or f"Launcher exited with code {returncode}"
            return _empty_launcher_status(script_path=script_path, error=message)

        data = json.loads(stdout or "{}")
        return _coerce_launcher_status(data)
    except Exception as exc:
        return _empty_launcher_status(script_path=script_path, error=str(exc))


def _compute_compatibility(request: Request, profile: str) -> RuntimeCompatibility:
    issues: list[str] = []
    local_task_model = str(getattr(request.app.state.config, "TASK_MODEL", "") or "").strip()
    external_task_model = str(
        getattr(request.app.state.config, "TASK_MODEL_EXTERNAL", "") or ""
    ).strip()
    planner_specialist_enabled = bool(
        getattr(request.app.state.config, "ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER", False)
    )

    if profile == "beast":
        if local_task_model or external_task_model:
            issues.append(
                "Task Model slots are configured while beast profile assumes no bounded specialist runtime."
            )
        if planner_specialist_enabled:
            issues.append(
                "Use Task Model For Planner is enabled while beast profile expects planner routing to stay on the active model."
            )
    elif profile == "dual":
        if not local_task_model and not external_task_model:
            issues.append(
                "Both Task Model slots are empty while dual profile expects a bounded specialist."
            )
        if not planner_specialist_enabled:
            issues.append(
                "Use Task Model For Planner is disabled while dual profile expects planner specialist routing."
            )
        if bool(local_task_model) != bool(external_task_model):
            issues.append(
                "Only one Task Model slot is configured; the other connection type will fall back to the active model."
            )
    else:
        issues.append(
            f"Runtime profile '{profile or 'manual'}' is outside the supported dual/beast V1 modes."
        )

    return RuntimeCompatibility(
        profile_compatibility="warning" if issues else "ok",
        issues=issues,
    )


def _derive_state(launcher_status: LauncherStatus) -> Literal[
    "stopped", "starting", "running", "stopping", "error"
]:
    transition_state = _transition_state.get("state")
    if transition_state in TRANSITION_RUNNING_STATES:
        return transition_state
    if launcher_status.running:
        return "running"
    if transition_state == "error" or launcher_status.error:
        return "error"
    return "stopped"


async def _build_runtime_status(request: Request) -> RuntimeStatusResponse:
    launcher_status = await _get_launcher_status(request)
    compatibility = _compute_compatibility(request, launcher_status.profile)
    last_error = _transition_state.get("last_error") or launcher_status.error or None
    return RuntimeStatusResponse(
        state=_derive_state(launcher_status),
        running=launcher_status.running,
        profile=launcher_status.profile,
        launcher_status=launcher_status,
        resolved_params=launcher_status.resolved_params,
        compatibility=compatibility,
        last_error=last_error,
        script_path=str(_script_path(request)),
    )


async def _run_mutation(
    request: Request,
    *,
    profile: Optional[str],
    transition_state: Literal["starting", "stopping"],
    command: list[str],
) -> RuntimeStatusResponse:
    if _action_lock.locked():
        status = await _build_runtime_status(request)
        status.last_error = "Another runtime action is already in progress."
        if status.state not in TRANSITION_RUNNING_STATES:
            status.state = "error"
        return status

    async with _action_lock:
        _transition_state["state"] = transition_state
        _transition_state["profile"] = profile
        _transition_state["last_error"] = None

        try:
            script_path = _script_path(request)
            returncode, stdout, stderr = await _run_launcher_command(script_path, *command)
            if returncode != 0:
                _transition_state["state"] = "error"
                _transition_state["last_error"] = stderr or stdout or f"Launcher exited with code {returncode}"
            else:
                _transition_state["state"] = None
                _transition_state["last_error"] = None
        except Exception as exc:
            log.exception("Runtime launcher command failed")
            _transition_state["state"] = "error"
            _transition_state["last_error"] = str(exc)

        status = await _build_runtime_status(request)
        if _transition_state.get("state") == "error":
            status.state = "error"
            status.last_error = _transition_state.get("last_error")
        return status


@router.get("/status", response_model=RuntimeStatusResponse)
async def get_runtime_status(
    request: Request,
    user=Depends(get_admin_user),
):
    return await _build_runtime_status(request)


@router.post("/start", response_model=RuntimeStatusResponse)
async def start_runtime(
    request: Request,
    form_data: RuntimeActionForm,
    user=Depends(get_admin_user),
):
    return await _run_mutation(
        request,
        profile=form_data.profile,
        transition_state="starting",
        command=["profile", form_data.profile],
    )


@router.post("/restart", response_model=RuntimeStatusResponse)
async def restart_runtime(
    request: Request,
    form_data: RuntimeActionForm,
    user=Depends(get_admin_user),
):
    return await _run_mutation(
        request,
        profile=form_data.profile,
        transition_state="starting",
        command=["restart-profile", form_data.profile],
    )


@router.post("/stop", response_model=RuntimeStatusResponse)
async def stop_runtime(
    request: Request,
    user=Depends(get_admin_user),
):
    return await _run_mutation(
        request,
        profile=None,
        transition_state="stopping",
        command=["stop"],
    )


@router.get("/logs", response_model=RuntimeLogsResponse)
async def get_runtime_logs(
    request: Request,
    lines: int = Query(200, ge=1, le=400),
    user=Depends(get_admin_user),
):
    script_path = _script_path(request)
    try:
        returncode, stdout, stderr = await _run_launcher_command(
            script_path, "logs", "--lines", str(lines)
        )
        if returncode != 0:
            return RuntimeLogsResponse(
                lines_requested=lines,
                error=stderr or stdout or f"Launcher exited with code {returncode}",
            )

        launcher_status = await _get_launcher_status(request)
        return RuntimeLogsResponse(
            log_file=launcher_status.log_file,
            lines_requested=lines,
            lines=stdout.splitlines(),
        )
    except Exception as exc:
        return RuntimeLogsResponse(lines_requested=lines, error=str(exc))
