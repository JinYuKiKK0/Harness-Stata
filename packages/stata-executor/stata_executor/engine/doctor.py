from __future__ import annotations

from ..contract import ConfigSource, DoctorResult, Edition, ExecutorDefaults
from ..runtime import RuntimeBootstrapError, resolve_configuration
from ..runtime.executable_resolver import resolve_stata_executable


def build_doctor_result(
    *,
    stata_executable: str | None = None,
    edition: Edition | None = None,
    config_source: ConfigSource | None = None,
) -> DoctorResult:
    try:
        resolved = resolve_configuration(
            stata_executable=stata_executable,
            edition=edition,
            source_override=config_source if config_source in {"explicit", "env", "missing"} else None,
        )
    except RuntimeBootstrapError as exc:
        return DoctorResult(
            ready=False,
            summary=f"Runtime configuration is invalid: {exc}",
            config_path="",
            config_exists=False,
            config_source="explicit" if stata_executable else "missing",
            stata_executable=stata_executable,
            edition=edition,
            defaults=ExecutorDefaults(),
            errors=[str(exc)],
        )

    executable = resolve_stata_executable(resolved.stata_executable, resolved.edition)
    if resolved.stata_executable is None:
        return DoctorResult(
            ready=False,
            summary="No Stata executable configured. Pass it explicitly via CLI argument or MCP environment.",
            config_path=str(resolved.config_path),
            config_exists=resolved.config_exists,
            config_source=resolved.config_source,
            stata_executable=None,
            edition=resolved.edition,
            defaults=resolved.defaults,
            errors=["Missing explicit 'stata_executable' input."],
        )
    if executable is None:
        return DoctorResult(
            ready=False,
            summary="Configured Stata executable could not be resolved.",
            config_path=str(resolved.config_path),
            config_exists=resolved.config_exists,
            config_source=resolved.config_source,
            stata_executable=resolved.stata_executable,
            edition=resolved.edition,
            defaults=resolved.defaults,
            errors=[f"Path does not resolve to a usable Stata executable: {resolved.stata_executable}"],
        )

    return DoctorResult(
        ready=True,
        summary="Stata executable resolved successfully.",
        config_path=str(resolved.config_path),
        config_exists=resolved.config_exists,
        config_source=resolved.config_source,
        stata_executable=str(executable),
        edition=resolved.edition,
        defaults=resolved.defaults,
        errors=[],
    )
