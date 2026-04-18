from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time

from ..runtime import ResolvedRuntime


@dataclass(frozen=True, slots=True)
class SubprocessOutcome:
    returncode: int
    elapsed_ms: int
    process_output: str
    process_text: str
    primary_text: str
    timed_out: bool
    start_error: str | None = None


def run_subprocess(runtime: ResolvedRuntime, command: list[str]) -> SubprocessOutcome:
    started_at = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=runtime.job_dir,
            env=runtime.env,
            capture_output=True,
            text=True,
            timeout=runtime.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        timeout_output = _compose_process_output(_coerce_text(exc.stdout), _coerce_text(exc.stderr))
        _, process_text = _finalize_process_log(runtime, timeout_output)
        run_text = _read_text(runtime.run_log_path)
        primary_text = run_text or process_text
        return SubprocessOutcome(
            returncode=124,
            elapsed_ms=elapsed_ms,
            process_output=timeout_output,
            process_text=process_text,
            primary_text=primary_text,
            timed_out=True,
        )
    except OSError as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        return SubprocessOutcome(
            returncode=1,
            elapsed_ms=elapsed_ms,
            process_output="",
            process_text="",
            primary_text="",
            timed_out=False,
            start_error=str(exc),
        )

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    process_output = _compose_process_output(completed.stdout, completed.stderr)
    _, process_text = _finalize_process_log(runtime, process_output)
    run_text = _read_text(runtime.run_log_path)
    primary_text = run_text or process_text
    return SubprocessOutcome(
        returncode=completed.returncode,
        elapsed_ms=elapsed_ms,
        process_output=process_output,
        process_text=process_text,
        primary_text=primary_text,
        timed_out=False,
    )


def _finalize_process_log(runtime: ResolvedRuntime, process_output: str) -> tuple[str | None, str]:
    raw_text = _read_text(runtime.raw_process_log_path)
    run_text = _read_text(runtime.run_log_path)

    if raw_text:
        normalized_raw = _normalize_for_dedup(raw_text)
        normalized_run = _normalize_for_dedup(run_text)
        if normalized_run and normalized_run in normalized_raw:
            runtime.raw_process_log_path.unlink(missing_ok=True)
            return None, ""

        if runtime.raw_process_log_path != runtime.process_log_path:
            if runtime.process_log_path.exists():
                runtime.process_log_path.unlink()
            runtime.raw_process_log_path.replace(runtime.process_log_path)
        return str(runtime.process_log_path), raw_text

    if process_output.strip():
        runtime.process_log_path.write_text(process_output, encoding="utf-8")
        return str(runtime.process_log_path), process_output

    return None, ""


def _compose_process_output(stdout: str | None, stderr: str | None) -> str:
    parts = [chunk.strip() for chunk in (stdout, stderr) if chunk and chunk.strip()]
    return "\n".join(parts)


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="ignore")
    return str(value)


def _normalize_for_dedup(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")
