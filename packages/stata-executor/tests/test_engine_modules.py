from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import textwrap
import time
import unittest

from stata_executor.engine.artifacts import collect_artifacts, snapshot_artifacts
from stata_executor.engine.output_parser import extract_diagnostics, parse_exit_code, render_result_text


class OutputParserTests(unittest.TestCase):
    def test_parse_exit_code_keeps_priority(self) -> None:
        text = "line\nr(610);\n__AGENT_RC__=199\n"
        self.assertEqual(parse_exit_code(text, fallback=1), 199)

        fallback_only = "nothing here"
        self.assertEqual(parse_exit_code(fallback_only, fallback=7), 7)

    def test_extract_diagnostics_strips_agent_rc(self) -> None:
        text = textwrap.dedent(
            """
            . regress y x
            variable x not found
            r(111);
            __AGENT_RC__=111
            """
        ).strip()

        excerpt, signature, failed_command = extract_diagnostics(text, exit_code=111)

        self.assertEqual(signature, "variable x not found")
        self.assertEqual(failed_command, "regress y x")
        self.assertNotIn("__AGENT_RC__", excerpt)

    def test_render_result_text_filters_noise_and_keeps_table(self) -> None:
        sample = textwrap.dedent(
            """
            . describe
            name:  agentlog
            Variable |        Obs        Mean
            ---------+-----------------------
                 x   |      1,500     .123

            __AGENT_RC__=0
            """
        ).strip()

        rendered = render_result_text(sample)

        self.assertIn("Variable |        Obs", rendered)
        self.assertNotIn(". describe", rendered)
        self.assertNotIn("name:  agentlog", rendered)


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="stata_executor_artifacts_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_collect_artifacts_returns_new_and_changed_sorted(self) -> None:
        output_dir = self._tmp / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        baseline = output_dir / "baseline.txt"
        baseline.write_text("before", encoding="utf-8")

        before = snapshot_artifacts(self._tmp, ("output/**/*.txt",))
        time.sleep(0.01)
        baseline.write_text("after", encoding="utf-8")
        created = output_dir / "new.txt"
        created.write_text("new", encoding="utf-8")

        artifacts = collect_artifacts(self._tmp, ("output/**/*.txt",), before)

        self.assertEqual(
            artifacts,
            [str(baseline.resolve()), str(created.resolve())],
        )


if __name__ == "__main__":
    unittest.main()
