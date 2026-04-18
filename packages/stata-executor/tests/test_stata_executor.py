from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import unittest
import uuid

from stata_executor import RunDoRequest, RunInlineRequest, StataExecutor
from stata_executor.engine.output_parser import render_result_text
from stata_executor.runtime.executable_resolver import build_stata_command, resolve_stata_executable

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StataExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._case_dirs: list[Path] = []

    def tearDown(self) -> None:
        if os.getenv("KEEP_TEST_ARTIFACTS", "0") == "1":
            return
        for case_dir in self._case_dirs:
            shutil.rmtree(case_dir, ignore_errors=True)

    def test_doctor_reports_missing_configuration(self) -> None:
        result = StataExecutor().doctor()

        self.assertFalse(result.ready)
        self.assertEqual(result.config_source, "missing")
        self.assertIn("No Stata executable configured", result.summary)

    def test_doctor_uses_explicit_config_and_resolves_executable(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        result = StataExecutor().doctor(stata_executable=str(fake_exe), edition="mp")

        self.assertTrue(result.ready)
        self.assertEqual(result.config_source, "explicit")
        self.assertTrue(result.stata_executable.endswith("fake_stata.cmd"))
        self.assertEqual(result.defaults.timeout_sec, 120)

    def test_executable_resolution_prefers_headless_candidate(self) -> None:
        root = self._workspace_case_dir()
        install_dir = root / "stata17"
        install_dir.mkdir(parents=True, exist_ok=True)
        gui = install_dir / "StataMP-64.exe"
        headless = install_dir / "StataMP-console.exe"
        gui.write_text("", encoding="utf-8")
        headless.write_text("", encoding="utf-8")

        resolved = resolve_stata_executable(str(install_dir), "mp")

        self.assertEqual(resolved, headless.resolve())

    def test_wrapper_command_keeps_windows_flags(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        wrapper = root / "wrapper.do"
        wrapper.write_text("", encoding="utf-8")

        command = build_stata_command(fake_exe, wrapper)

        if sys.platform.startswith("win"):
            self.assertEqual(command[1:4], ["/q", "/i", "/e"])

    def test_missing_script_returns_input_error(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)

        result = StataExecutor().run_do(
            RunDoRequest(
                script_path="missing.do",
                working_dir=str(root / "wd"),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.phase, "input")
        self.assertEqual(result.error_kind, "input_error")
        result_files = list((root / "wd" / ".stata-executor" / "jobs").glob("*/result.json"))
        self.assertEqual(len(result_files), 1)

    def test_run_inline_reports_parse_error(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)

        result = StataExecutor().run_inline(
            RunInlineRequest(
                commands="FAKE_ERROR 199|command foo is unrecognized",
                working_dir=str(root / "wd"),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_kind, "stata_parse_or_command_error")
        self.assertIn("command foo is unrecognized", result.summary)
        self.assertIn("command foo is unrecognized", result.result_text)

    def test_failed_job_returns_mechanical_diagnostics(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)

        result = StataExecutor().run_inline(
            RunInlineRequest(
                commands="regress price weight mpg\nFAKE_ERROR 111|variable mpg not found",
                working_dir=str(root / "wd"),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(result.error_signature, "variable mpg not found")
        self.assertEqual(result.failed_command, "regress price weight mpg")
        self.assertNotIn(". regress price weight mpg", result.result_text)
        self.assertIn("variable mpg not found", result.result_text)
        self.assertIn(". regress price weight mpg", result.diagnostic_excerpt)
        self.assertNotIn("__AGENT_RC__", result.diagnostic_excerpt)

    def test_success_job_collects_artifacts(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        working_dir = root / "wd"

        result = StataExecutor().run_inline(
            RunInlineRequest(
                commands="FAKE_WRITE output/result.txt",
                working_dir=str(working_dir),
                artifact_globs=("output/**/*.txt",),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.phase, "completed")
        self.assertEqual(result.artifacts, [str((working_dir / "output" / "result.txt").resolve())])
        self.assertIn("wrote", result.result_text)
        self.assertEqual(len(list((working_dir / ".stata-executor" / "jobs").glob("*/result.json"))), 1)

    def test_result_text_keeps_tables_and_drops_execution_noise(self) -> None:
        sample_log = textwrap.dedent(
            """
            -------------------------------------------------------------------------------
                  name:  agentlog
                   log:  D:/work/project/.stata-executor/jobs/job_x/run.log
              log type:  text
             opened on:  22 Mar 2026, 22:38:32

            . cd "D:/work/project"
            D:\\work\\project

            . destring city_code year, replace
            city_code already numeric; no replace

            . outreg2 using "01-描述性统计.rtf", replace

                Variable |        Obs        Mean    Std. dev.       Min        Max
            -------------+---------------------------------------------------------
                      FR |      1,500     .776256    .2033553   .4086425   1.340913
                    DIFI |      1,500    2.649383    .5420632    1.55995   3.885055

            Following variable is string, not included:
            city
            01-描述性统计.rtf
            dir : seeout

            . xtreg FR DIFI GDP OPEN INFRA i.year, fe

            Fixed-effects (within) regression               Number of obs     =      1,500
            Group variable: city_code                       Number of groups  =        150

            R-squared:                                      Obs per group:
                 Within  = 0.5200                                         min =         10
                 Between = 0.2472                                         avg =       10.0
                 Overall = 0.1760                                         max =         10

                                                            F(13,1337)        =     111.41
            corr(u_i, Xb) = 0.1065                          Prob > F          =     0.0000

            ------------------------------------------------------------------------------
                      FR | Coefficient  Std. err.      t    P>|t|     [95% conf. interval]
            -------------+----------------------------------------------------------------
                    DIFI |   .0462398   .0291576     1.59   0.113    -.0109599    .1034395
                   _cons |   .4577587   .1945558     2.35   0.019     .0760908    .8394265
            -------------+----------------------------------------------------------------
                 sigma_u |  .17538119
                 sigma_e |  .06567415
                     rho |  .87702054   (fraction of variance due to u_i)
            ------------------------------------------------------------------------------
            F test that all u_i=0: F(149, 1337) = 55.16                  Prob > F = 0.0000

            end of do-file
            __AGENT_RC__=0
            """
        ).strip()

        rendered = render_result_text(sample_log)

        self.assertIn("Variable |        Obs", rendered)
        self.assertIn("Fixed-effects (within) regression", rendered)
        self.assertIn("F test that all u_i=0", rendered)
        self.assertNotIn("D:\\work\\project", rendered)
        self.assertNotIn("already numeric", rendered)
        self.assertNotIn("dir : seeout", rendered)
        self.assertNotIn("end of do-file", rendered)

    def test_failed_job_still_collects_artifacts(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        working_dir = root / "wd"

        result = StataExecutor().run_inline(
            RunInlineRequest(
                commands="FAKE_WRITE reports/partial.txt\nFAKE_ERROR 199|command foo is unrecognized",
                working_dir=str(working_dir),
                artifact_globs=("reports/**/*.txt",),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.artifacts, [str((working_dir / "reports" / "partial.txt").resolve())])

    def test_timeout_terminates_subprocess_and_next_job_is_clean(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        executor = StataExecutor()

        timed_out = executor.run_inline(
            RunInlineRequest(
                commands="FAKE_SLEEP 2",
                working_dir=str(root / "wd"),
                timeout_sec=1,
                stata_executable=str(fake_exe),
            )
        )
        succeeded = executor.run_inline(
            RunInlineRequest(
                commands="FAKE_WRITE reports/ok.txt",
                working_dir=str(root / "wd"),
                timeout_sec=5,
                artifact_globs=("reports/**/*.txt",),
                stata_executable=str(fake_exe),
            )
        )

        self.assertEqual(timed_out.error_kind, "timeout")
        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(len(list((root / "wd" / ".stata-executor" / "jobs").glob("*/result.json"))), 2)

    def test_cli_doctor_returns_json_and_exit_code(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        completed = subprocess.run(
            [sys.executable, "-m", "stata_executor", "doctor", "--stata-executable", str(fake_exe)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.stderr.strip(), "")
        payload = json.loads(completed.stdout)
        self.assertIn("ready", payload)
        self.assertIn(completed.returncode, {0, 1})

    def test_cli_argument_errors_return_stable_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "stata_executor", "run-inline", "--stata-executable", "", "--env", "INVALID"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["phase"], "input")
        self.assertEqual(payload["error_kind"], "input_error")

    def test_mcp_server_lists_tools_and_runs_inline(self) -> None:
        root = self._workspace_case_dir()
        fake_exe = self._create_fake_stata_executable(root)
        working_dir = root / "wd"
        process_env = dict(os.environ)
        process_env["STATA_EXECUTOR_STATA_EXECUTABLE"] = str(fake_exe)
        process = subprocess.Popen(
            [sys.executable, "-m", "stata_executor.adapters.mcp"],
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=process_env,
        )
        try:
            self._send_mcp(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
            )
            init_response = self._read_mcp(process)
            self.assertEqual(init_response["result"]["serverInfo"]["name"], "stata-executor")

            self._send_mcp(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._send_mcp(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            list_response = self._read_mcp(process)
            names = {tool["name"] for tool in list_response["result"]["tools"]}
            self.assertEqual(names, {"doctor", "run_do", "run_inline"})

            self._send_mcp(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "run_inline",
                        "arguments": {
                            "commands": "FAKE_WRITE output/result.txt",
                            "working_dir": str(working_dir),
                            "artifact_globs": ["output/**/*.txt"],
                        },
                    },
                },
            )
            run_response = self._read_mcp(process)
            self.assertFalse(run_response["result"]["isError"])
            self.assertEqual(
                run_response["result"]["structuredContent"]["artifacts"],
                [str((working_dir / "output" / "result.txt").resolve())],
            )
            self.assertIn("wrote", run_response["result"]["structuredContent"]["result_text"])
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.terminate()
            process.wait(timeout=5)

    def _create_fake_stata_executable(self, root: Path) -> Path:
        fake_py = root / "fake_stata.py"
        fake_py.write_text(
            textwrap.dedent(
                """
                from __future__ import annotations

                import re
                from pathlib import Path
                import sys
                import time


                def parse_wrapper(path: Path) -> tuple[Path, Path, Path]:
                    text = path.read_text(encoding="utf-8")
                    run_log_match = re.search(r'log using "([^"]+)"', text)
                    cwd_match = re.search(r'cd "([^"]+)"', text)
                    do_match = re.search(r'do "([^"]+)"', text)
                    if not run_log_match or not cwd_match or not do_match:
                        raise RuntimeError("wrapper format changed")
                    return Path(run_log_match.group(1)), Path(cwd_match.group(1)), Path(do_match.group(1))


                def main() -> int:
                    wrapper = Path(sys.argv[-1])
                    run_log_path, working_dir, input_do = parse_wrapper(wrapper)
                    working_dir.mkdir(parents=True, exist_ok=True)
                    commands = input_do.read_text(encoding="utf-8")
                    lines = []
                    rc = 0

                    for raw in commands.splitlines():
                        line = raw.strip()
                        if not line:
                            continue
                        if line.startswith("FAKE_SLEEP "):
                            time.sleep(float(line.split(" ", 1)[1]))
                            continue
                        if line.startswith("FAKE_WRITE "):
                            target = working_dir / line.split(" ", 1)[1]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text("ok", encoding="utf-8")
                            lines.append(f"wrote {target}")
                            continue
                        if line.startswith("FAKE_ERROR "):
                            payload = line.split(" ", 1)[1]
                            code_text, message = payload.split("|", 1)
                            rc = int(code_text)
                            lines.append(message)
                            lines.append(f"r({rc});")
                            break
                        lines.append(f". {line}")

                    lines.append(f"__AGENT_RC__={rc}")
                    run_log_path.parent.mkdir(parents=True, exist_ok=True)
                    run_log_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")

                    process_log = Path.cwd() / f"{wrapper.stem}.log"
                    process_lines = ["outer process header", *lines]
                    process_log.write_text("\\n".join(process_lines) + "\\n", encoding="utf-8")
                    return rc


                if __name__ == "__main__":
                    raise SystemExit(main())
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        fake_cmd = root / "fake_stata.cmd"
        fake_cmd.write_text(
            f'@echo off\r\n"{sys.executable}" "%~dp0fake_stata.py" %*\r\n',
            encoding="utf-8",
        )
        return fake_cmd

    def _send_mcp(self, process: subprocess.Popen[str], payload: dict[str, object]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _read_mcp(self, process: subprocess.Popen[str]) -> dict[str, object]:
        assert process.stdout is not None
        line = process.stdout.readline().strip()
        if not line:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"Expected MCP response, got EOF. stderr={stderr!r}")
        return json.loads(line)

    def _workspace_case_dir(self) -> Path:
        base = Path.cwd() / ".tmp_test_runs"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"case_{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=False)
        self._case_dirs.append(root)
        return root


if __name__ == "__main__":
    unittest.main()
