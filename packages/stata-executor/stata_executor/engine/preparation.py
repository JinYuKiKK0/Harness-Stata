from __future__ import annotations

from pathlib import Path
import shutil

from ..runtime import ResolvedRuntime


def validate_request(timeout_sec: int | None, artifact_globs: tuple[str, ...]) -> str | None:
    if timeout_sec is not None and timeout_sec <= 0:
        return "timeout_sec must be a positive integer when provided."
    if any(Path(pattern).is_absolute() for pattern in artifact_globs):
        return "artifact_globs must be relative to working_dir."
    return None


def resolve_user_path(path_like: str, working_dir: Path) -> Path:
    path = Path(path_like)
    if not path.is_absolute():
        path = working_dir / path
    return path.resolve()


def stage_do_input(runtime: ResolvedRuntime, script_path: Path) -> None:
    shutil.copy2(script_path, runtime.input_do_path)


def stage_inline_input(runtime: ResolvedRuntime, commands: str) -> None:
    normalized = commands if commands.endswith("\n") else f"{commands}\n"
    runtime.input_do_path.write_text(normalized, encoding="utf-8")


def write_wrapper_do(runtime: ResolvedRuntime) -> None:
    wrapper = "\n".join(
        [
            "version 17.0",
            "clear all",
            "set more off",
            "capture log close _all",
            f'log using "{runtime.run_log_path.as_posix()}", replace text name(agentlog)',
            f'cd "{runtime.working_dir.as_posix()}"',
            f'capture noisily do "{runtime.input_do_path.as_posix()}"',
            "local agent_rc = _rc",
            'display "__AGENT_RC__=`agent_rc\'"',
            "capture log close agentlog",
            "exit `agent_rc', STATA clear",
            "",
        ]
    )
    runtime.wrapper_do_path.write_text(wrapper, encoding="utf-8")
