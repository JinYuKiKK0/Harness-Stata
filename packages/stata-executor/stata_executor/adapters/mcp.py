from __future__ import annotations

import json
import os
import sys
from typing import Any

from ..contract import RunDoRequest, RunInlineRequest
from ..engine import StataExecutor


SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {
    "name": "stata-executor",
    "title": "Stata Executor",
    "version": "0.1.0",
}


class MCPServer:
    def __init__(self) -> None:
        self._executor = StataExecutor()
        self._initialized = False
        self._env_stata_executable = os.environ.get("STATA_EXECUTOR_STATA_EXECUTABLE")
        self._env_edition, self._env_error = _parse_env_edition(os.environ.get("STATA_EXECUTOR_EDITION"))

    def serve(self) -> int:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._write_error(None, -32700, "Invalid JSON.")
                continue
            self._handle_message(message)
        return 0

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})

        if method == "initialize":
            self._handle_initialize(request_id, params)
            return
        if method == "notifications/initialized":
            self._initialized = True
            return
        if method == "ping":
            self._write_result(request_id, {})
            return
        if method == "tools/list":
            self._write_result(request_id, {"tools": _tool_definitions()})
            return
        if method == "tools/call":
            self._handle_tool_call(request_id, params)
            return
        if request_id is not None:
            self._write_error(request_id, -32601, f"Unsupported method: {method}")

    def _handle_initialize(self, request_id: Any, params: dict[str, Any]) -> None:
        requested_version = params.get("protocolVersion")
        if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
            self._write_error(
                request_id,
                -32602,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": requested_version},
            )
            return

        self._write_result(
            request_id,
            {
                "protocolVersion": requested_version,
                "capabilities": {
                    "tools": {
                        "listChanged": False,
                    }
                },
                "serverInfo": SERVER_INFO,
                "instructions": "Use doctor before run_do or run_inline when configuration is uncertain.",
            },
        )

    def _handle_tool_call(self, request_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            self._write_error(request_id, -32602, "Tool arguments must be an object.")
            return
        if self._env_error:
            self._write_error(request_id, -32602, self._env_error)
            return

        try:
            if name == "doctor":
                result = self._executor.doctor(
                    stata_executable=self._env_stata_executable,
                    edition=self._env_edition,
                    config_source="env" if self._env_stata_executable else "missing",
                )
                self._write_result(request_id, _tool_result(result.to_dict(), is_error=False))
                return
            if name == "run_do":
                execution = self._executor.run_do(
                    RunDoRequest(
                        script_path=_require_string(arguments, "script_path"),
                        working_dir=_string_or_none(arguments.get("working_dir")),
                        timeout_sec=_int_or_none(arguments.get("timeout_sec")),
                        artifact_globs=tuple(_string_list(arguments.get("artifact_globs"))),
                        edition=self._env_edition,
                        stata_executable=self._env_stata_executable,
                        env_overrides=_string_map(arguments.get("env_overrides")),
                    )
                )
                self._write_result(request_id, _tool_result(execution.to_dict(), is_error=execution.status == "failed"))
                return
            if name == "run_inline":
                execution = self._executor.run_inline(
                    RunInlineRequest(
                        commands=_require_string(arguments, "commands"),
                        working_dir=_string_or_none(arguments.get("working_dir")),
                        timeout_sec=_int_or_none(arguments.get("timeout_sec")),
                        artifact_globs=tuple(_string_list(arguments.get("artifact_globs"))),
                        edition=self._env_edition,
                        stata_executable=self._env_stata_executable,
                        env_overrides=_string_map(arguments.get("env_overrides")),
                    )
                )
                self._write_result(request_id, _tool_result(execution.to_dict(), is_error=execution.status == "failed"))
                return
            self._write_error(request_id, -32602, f"Unknown tool: {name}")
        except ValueError as exc:
            self._write_error(request_id, -32602, str(exc))

    def _write_result(self, request_id: Any, result: dict[str, Any]) -> None:
        self._write_message({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _write_error(self, request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        self._write_message({"jsonrpc": "2.0", "id": request_id, "error": error})

    def _write_message(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main() -> int:
    return MCPServer().serve()


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "doctor",
            "title": "Doctor",
            "description": "Validate MCP environment configuration and resolve the Stata executable path.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
            "outputSchema": _doctor_output_schema(),
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": "run_do",
            "title": "Run Do-File",
            "description": "Execute an existing Stata do-file and return the factual execution result.",
            "inputSchema": _execution_input_schema(required=["script_path"]),
            "outputSchema": _execution_output_schema(),
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
        {
            "name": "run_inline",
            "title": "Run Inline Commands",
            "description": "Execute inline Stata commands and return the factual execution result.",
            "inputSchema": _execution_input_schema(required=["commands"]),
            "outputSchema": _execution_output_schema(),
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
    ]


def _execution_input_schema(*, required: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "working_dir": {"type": "string"},
        "timeout_sec": {"type": "integer", "minimum": 1},
        "artifact_globs": {"type": "array", "items": {"type": "string"}},
        "env_overrides": {"type": "object", "additionalProperties": {"type": "string"}},
    }
    if "script_path" in required:
        properties["script_path"] = {"type": "string"}
    if "commands" in required:
        properties["commands"] = {"type": "string"}
    return {"type": "object", "properties": properties, "required": required}


def _execution_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["succeeded", "failed"]},
            "phase": {"type": "string"},
            "exit_code": {"type": "integer"},
            "error_kind": {"type": ["string", "null"]},
            "summary": {"type": "string"},
            "result_text": {"type": "string"},
            "diagnostic_excerpt": {"type": "string"},
            "error_signature": {"type": ["string", "null"]},
            "failed_command": {"type": ["string", "null"]},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "elapsed_ms": {"type": "integer"},
        },
        "required": [
            "status",
            "phase",
            "exit_code",
            "error_kind",
            "summary",
            "result_text",
            "diagnostic_excerpt",
            "error_signature",
            "failed_command",
            "artifacts",
            "elapsed_ms",
        ],
    }


def _doctor_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ready": {"type": "boolean"},
            "summary": {"type": "string"},
            "config_path": {"type": "string"},
            "config_exists": {"type": "boolean"},
            "config_source": {"type": "string", "enum": ["explicit", "env", "missing"]},
            "stata_executable": {"type": ["string", "null"]},
            "edition": {"type": ["string", "null"]},
            "defaults": {
                "type": "object",
                "properties": {
                    "timeout_sec": {"type": "integer"},
                    "artifact_globs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["timeout_sec", "artifact_globs"],
            },
            "errors": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "ready",
            "summary",
            "config_path",
            "config_exists",
            "config_source",
            "stata_executable",
            "edition",
            "defaults",
            "errors",
        ],
    }


def _require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string.")
    return value


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string value.")
    return value


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError("Expected an integer value.")
    return value


def _parse_env_edition(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if value not in {"mp", "se", "be"}:
        return None, "STATA_EXECUTOR_EDITION must be one of: mp, se, be."
    return value, None


def _string_map(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError("Expected an object of string key-value pairs.")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Expected an array of strings.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
