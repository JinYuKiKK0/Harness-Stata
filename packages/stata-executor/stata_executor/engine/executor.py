from __future__ import annotations

from dataclasses import replace

from ..contract import ConfigSource, DoctorResult, Edition, ErrorKind, ExecutionPhase, ExecutionResult, RunDoRequest, RunInlineRequest
from ..runtime import RuntimeBootstrapError, ResolvedRuntime, prepare_runtime
from ..runtime.executable_resolver import build_stata_command, resolve_stata_executable
from .artifacts import collect_artifacts, snapshot_artifacts
from .doctor import build_doctor_result
from .output_parser import (
    build_bootstrap_summary,
    build_execution_summary,
    classify_execution_failure,
    extract_diagnostics,
    extract_last_meaningful_line,
    parse_exit_code,
    render_result_text,
    strip_agent_rc_trailer_text,
)
from .preparation import resolve_user_path, stage_do_input, stage_inline_input, validate_request, write_wrapper_do
from .process_runner import run_subprocess


class StataExecutor:
    """Zero-dependency Stata execution kernel with CLI and MCP adapters layered on top."""

    def doctor(
        self,
        *,
        stata_executable: str | None = None,
        edition: Edition | None = None,
        config_source: ConfigSource | None = None,
    ) -> DoctorResult:
        return build_doctor_result(
            stata_executable=stata_executable,
            edition=edition,
            config_source=config_source,
        )

    def run_do(self, request: RunDoRequest) -> ExecutionResult:
        validation_error = validate_request(request.timeout_sec, request.artifact_globs)
        if validation_error is not None:
            return self._make_failed_result(
                phase="input",
                exit_code=2,
                error_kind="input_error",
                summary=validation_error,
            )

        try:
            runtime = prepare_runtime(request)
        except RuntimeBootstrapError as exc:
            return self._make_failed_result(
                phase="bootstrap",
                exit_code=1,
                error_kind="bootstrap_error",
                summary=str(exc),
            )

        script = resolve_user_path(request.script_path, runtime.working_dir)
        if not script.exists():
            return self._persist_result(
                runtime,
                self._make_failed_result(
                    phase="input",
                    exit_code=601,
                    error_kind="input_error",
                    summary=f"Script does not exist: {script}",
                ),
            )

        stage_do_input(runtime, script)
        return self._execute_prepared_job(runtime)

    def run_inline(self, request: RunInlineRequest) -> ExecutionResult:
        validation_error = validate_request(request.timeout_sec, request.artifact_globs)
        if validation_error is not None:
            return self._make_failed_result(
                phase="input",
                exit_code=2,
                error_kind="input_error",
                summary=validation_error,
            )
        if not request.commands.strip():
            return self._make_failed_result(
                phase="input",
                exit_code=2,
                error_kind="input_error",
                summary="Inline execution requires non-empty commands.",
            )

        try:
            runtime = prepare_runtime(request)
        except RuntimeBootstrapError as exc:
            return self._make_failed_result(
                phase="bootstrap",
                exit_code=1,
                error_kind="bootstrap_error",
                summary=str(exc),
            )

        stage_inline_input(runtime, request.commands)
        return self._execute_prepared_job(runtime)

    def _execute_prepared_job(self, runtime: ResolvedRuntime) -> ExecutionResult:
        write_wrapper_do(runtime)
        before_snapshot = snapshot_artifacts(runtime.working_dir, runtime.artifact_globs)
        result = self._run_subprocess_job(runtime)
        try:
            artifacts = collect_artifacts(runtime.working_dir, runtime.artifact_globs, before_snapshot)
        except OSError as exc:
            if result.status == "failed":
                return self._persist_result(
                    runtime,
                    replace(
                        result,
                        summary=f"{result.summary} Artifact collection also failed: {exc}",
                        artifacts=[],
                    ),
                )
            return self._persist_result(
                runtime,
                replace(
                    result,
                    status="failed",
                    phase="collect",
                    error_kind="artifact_collection_error",
                    summary=f"Artifact collection failed: {exc}",
                    artifacts=[],
                ),
            )

        if result.status == "failed":
            return self._persist_result(runtime, replace(result, artifacts=artifacts))
        return self._persist_result(
            runtime,
            replace(
                result,
                status="succeeded",
                phase="completed",
                error_kind=None,
                artifacts=artifacts,
            ),
        )

    def _run_subprocess_job(self, runtime: ResolvedRuntime) -> ExecutionResult:
        executable = resolve_stata_executable(runtime.config.stata_executable, runtime.config.edition)
        if executable is None:
            return self._make_failed_result(
                phase="bootstrap",
                exit_code=1,
                error_kind="bootstrap_error",
                summary="Unable to resolve a Stata executable from explicit input.",
            )

        outcome = run_subprocess(runtime, build_stata_command(executable, runtime.wrapper_do_path))
        if outcome.timed_out:
            result_text = render_result_text(outcome.primary_text)
            diagnostic_excerpt, error_signature, failed_command = extract_diagnostics(outcome.primary_text, exit_code=124)
            return self._make_failed_result(
                phase="execute",
                exit_code=124,
                error_kind="timeout",
                summary=f"Execution timed out after {runtime.timeout_sec}s and the subprocess was terminated.",
                result_text=result_text,
                diagnostic_excerpt=diagnostic_excerpt,
                error_signature=error_signature,
                failed_command=failed_command,
                elapsed_ms=outcome.elapsed_ms,
            )

        if outcome.start_error is not None:
            return self._make_failed_result(
                phase="bootstrap",
                exit_code=1,
                error_kind="bootstrap_error",
                summary=f"Failed to start Stata subprocess: {outcome.start_error}",
                result_text="",
                elapsed_ms=outcome.elapsed_ms,
            )

        exit_code = parse_exit_code(outcome.primary_text, fallback=outcome.returncode)
        result_text = render_result_text(outcome.primary_text)
        diagnostic_excerpt, error_signature, failed_command = extract_diagnostics(outcome.primary_text, exit_code)

        if outcome.returncode != 0 and not outcome.primary_text.strip():
            fallback_text = outcome.process_text or outcome.process_output
            return self._make_failed_result(
                phase="bootstrap",
                exit_code=outcome.returncode or 1,
                error_kind="bootstrap_error",
                summary=build_bootstrap_summary(outcome.process_output),
                result_text=render_result_text(fallback_text),
                diagnostic_excerpt=strip_agent_rc_trailer_text(fallback_text),
                error_signature=extract_last_meaningful_line(fallback_text),
                elapsed_ms=outcome.elapsed_ms,
            )

        error_kind = None if exit_code == 0 else classify_execution_failure(outcome.primary_text, exit_code)
        return ExecutionResult(
            status="succeeded" if exit_code == 0 else "failed",
            phase="completed" if exit_code == 0 else "execute",
            exit_code=exit_code,
            error_kind=error_kind,
            summary=build_execution_summary(outcome.primary_text, exit_code),
            result_text=result_text,
            diagnostic_excerpt=diagnostic_excerpt,
            error_signature=error_signature,
            failed_command=failed_command,
            artifacts=[],
            elapsed_ms=outcome.elapsed_ms,
        )

    def _make_failed_result(
        self,
        *,
        phase: ExecutionPhase,
        exit_code: int,
        error_kind: ErrorKind,
        summary: str,
        result_text: str = "",
        diagnostic_excerpt: str = "",
        error_signature: str | None = None,
        failed_command: str | None = None,
        elapsed_ms: int = 0,
    ) -> ExecutionResult:
        return ExecutionResult(
            status="failed",
            phase=phase,
            exit_code=exit_code,
            error_kind=error_kind,
            summary=summary,
            result_text=result_text,
            diagnostic_excerpt=diagnostic_excerpt,
            error_signature=error_signature,
            failed_command=failed_command,
            artifacts=[],
            elapsed_ms=elapsed_ms,
        )

    def _persist_result(self, runtime: ResolvedRuntime, result: ExecutionResult) -> ExecutionResult:
        runtime.result_path.write_text(result.to_json(pretty=True), encoding="utf-8")
        return result


def run_do(request: RunDoRequest) -> ExecutionResult:
    return StataExecutor().run_do(request)


def run_inline(request: RunInlineRequest) -> ExecutionResult:
    return StataExecutor().run_inline(request)


def doctor(*, stata_executable: str | None = None, edition: Edition | None = None) -> DoctorResult:
    return StataExecutor().doctor(stata_executable=stata_executable, edition=edition)
