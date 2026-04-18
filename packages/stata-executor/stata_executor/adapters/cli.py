from __future__ import annotations

import argparse
import json
import sys

from ..contract import RunDoRequest, RunInlineRequest
from ..engine import StataExecutor


class CLIArgumentError(Exception):
    """Raised when CLI arguments cannot be parsed into a valid request."""


class StableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = StableArgumentParser(description="Zero-dependency Stata executor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate CLI arguments and resolve the Stata executable")
    doctor.add_argument("--stata-executable", type=str, required=True)
    doctor.add_argument("--edition", type=str, default=None, choices=["mp", "se", "be"])
    doctor.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    run_do = subparsers.add_parser("run-do", help="Run an existing do-file as an isolated job")
    _add_execution_arguments(run_do)
    run_do.add_argument("script_path", type=str)

    run_inline = subparsers.add_parser("run-inline", help="Run inline Stata commands as an isolated job")
    _add_execution_arguments(run_inline)
    run_inline.add_argument("commands", nargs="?", type=str)

    return parser


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--working-dir", type=str, default=None)
    parser.add_argument("--timeout-sec", type=int, default=None)
    parser.add_argument("--artifact-glob", action="append", default=[])
    parser.add_argument("--edition", type=str, default=None, choices=["mp", "se", "be"])
    parser.add_argument("--stata-executable", type=str, required=True)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")


def main(argv: list[str] | None = None) -> int:
    executor = StataExecutor()
    try:
        args = build_parser().parse_args(argv)
        if args.command == "doctor":
            result = executor.doctor(
                stata_executable=args.stata_executable,
                edition=args.edition,
            )
            print(result.to_json(pretty=args.pretty))
            return 0 if result.ready else 1

        env_overrides = _parse_env_overrides(args.env)
        if args.command == "run-do":
            result = executor.run_do(
                RunDoRequest(
                    script_path=args.script_path,
                    working_dir=args.working_dir,
                    timeout_sec=args.timeout_sec,
                    artifact_globs=tuple(args.artifact_glob),
                    edition=args.edition,
                    stata_executable=args.stata_executable,
                    env_overrides=env_overrides,
                )
            )
        else:
            commands = args.commands if args.commands is not None else sys.stdin.read()
            result = executor.run_inline(
                RunInlineRequest(
                    commands=commands,
                    working_dir=args.working_dir,
                    timeout_sec=args.timeout_sec,
                    artifact_globs=tuple(args.artifact_glob),
                    edition=args.edition,
                    stata_executable=args.stata_executable,
                    env_overrides=env_overrides,
                )
            )

        print(result.to_json(pretty=args.pretty))
        return 0 if result.status == "succeeded" else (result.exit_code or 1)
    except (CLIArgumentError, ValueError) as exc:
        payload = {
            "status": "failed",
            "phase": "input",
            "exit_code": 2,
            "error_kind": "input_error",
            "summary": f"CLI argument error: {exc}",
            "result_text": "",
            "diagnostic_excerpt": "",
            "error_signature": None,
            "failed_command": None,
            "artifacts": [],
            "elapsed_ms": 0,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 2


def _parse_env_overrides(values: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"Invalid --env value: {raw!r}. Expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        if not key:
            raise ValueError("Environment override key cannot be empty.")
        env[key] = value
    return env
