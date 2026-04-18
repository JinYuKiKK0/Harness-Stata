from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
import uuid

from ..contract import ConfigSource, Edition, ExecutorDefaults, RunDoRequest, RunInlineRequest


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    config_path: Path
    config_exists: bool
    config_source: ConfigSource
    stata_executable: str | None
    edition: Edition
    defaults: ExecutorDefaults


@dataclass(frozen=True, slots=True)
class ResolvedRuntime:
    config: ResolvedConfiguration
    working_dir: Path
    job_id: str
    job_dir: Path
    input_do_path: Path
    wrapper_do_path: Path
    run_log_path: Path
    raw_process_log_path: Path
    process_log_path: Path
    result_path: Path
    timeout_sec: int
    artifact_globs: tuple[str, ...]
    env: dict[str, str]


class RuntimeBootstrapError(ValueError):
    """Raised when runtime defaults cannot be resolved."""


def resolve_configuration(
    *,
    stata_executable: str | None,
    edition: Edition | None,
    source_override: ConfigSource | None = None,
) -> ResolvedConfiguration:
    source: ConfigSource = source_override or ("explicit" if stata_executable else "missing")
    resolved_edition = _resolve_edition(edition)

    return ResolvedConfiguration(
        config_path=Path(""),
        config_exists=False,
        config_source=source,
        stata_executable=stata_executable,
        edition=resolved_edition,
        defaults=ExecutorDefaults(),
    )


def _resolve_edition(edition: Edition | None) -> Edition:
    if edition is None:
        return "mp"
    if edition not in {"mp", "se", "be"}:
        raise RuntimeBootstrapError("Edition must be one of: mp, se, be.")
    return edition


def prepare_runtime(request: RunDoRequest | RunInlineRequest) -> ResolvedRuntime:
    config = resolve_configuration(
        stata_executable=request.stata_executable,
        edition=request.edition,
    )
    working_dir = _resolve_working_dir(request.working_dir)
    timeout_sec = request.timeout_sec or config.defaults.timeout_sec
    artifact_globs = tuple(request.artifact_globs) if request.artifact_globs else config.defaults.artifact_globs
    env = dict(os.environ)
    env.update(request.env_overrides)

    job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    job_dir = working_dir / ".stata-executor" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)

    return ResolvedRuntime(
        config=config,
        working_dir=working_dir,
        job_id=job_id,
        job_dir=job_dir,
        input_do_path=job_dir / "input.do",
        wrapper_do_path=job_dir / "wrapper.do",
        run_log_path=job_dir / "run.log",
        raw_process_log_path=job_dir / "wrapper.log",
        process_log_path=job_dir / "process.log",
        result_path=job_dir / "result.json",
        timeout_sec=timeout_sec,
        artifact_globs=artifact_globs,
        env=env,
    )


def _resolve_working_dir(path_like: str | None) -> Path:
    base = Path(path_like) if path_like else Path.cwd()
    resolved = base.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved
