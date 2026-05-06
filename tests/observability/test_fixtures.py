"""Fixture-as-contract integrity checks for ``downloads/fixtures/<sub>/``.

Each fixture's ``input_state.json`` must carry the slices required by every
node it feeds. We assert the three-way agreement of:

* ``merged_dataset`` against the on-disk ``merged.csv``
* ``model_plan.core_hypothesis`` against ``empirical_spec.variables``
* ``model_plan.equation`` against ``core_hypothesis.variable_name``

Pure-logic only — no LLM/MCP calls.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _PROJECT_ROOT / "downloads" / "fixtures"
_FIXTURE_SUBDIRS = (
    "01_capital_structure_roa",
    "02_digital_finance_liquidity",
    "03_fintech_bank_npl",
)
_REQUIRED_TOP_LEVEL_KEYS = (
    "empirical_spec",
    "downloaded_files",
    "merged_dataset",
    "model_plan",
)
_VALID_EXPECTED_SIGNS = {"+", "-", "ambiguous"}


def _load_fixture(subdir: str) -> dict:
    path = _FIXTURES_DIR / subdir / "input_state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv_header_and_count(path: Path) -> tuple[list[str], int]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        row_count = sum(1 for _ in reader)
    return headers, row_count


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_input_state_has_required_top_level_keys(subdir: str) -> None:
    state = _load_fixture(subdir)
    for key in _REQUIRED_TOP_LEVEL_KEYS:
        assert key in state, f"{subdir}: missing top-level key {key!r}"


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_merged_dataset_file_path_points_to_sibling_merged_csv(subdir: str) -> None:
    state = _load_fixture(subdir)
    file_path_str = state["merged_dataset"]["file_path"]
    file_path = Path(file_path_str)
    assert file_path.is_absolute(), f"{subdir}: merged_dataset.file_path not absolute"
    assert file_path.is_file(), f"{subdir}: merged.csv missing at {file_path}"
    assert file_path.name == "merged.csv"
    assert file_path.parent == _FIXTURES_DIR / subdir, (
        f"{subdir}: merged.csv must sit in fixture root, got {file_path.parent}"
    )


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_merged_dataset_columns_and_row_count_match_csv(subdir: str) -> None:
    state = _load_fixture(subdir)
    merged = state["merged_dataset"]
    headers, row_count = _read_csv_header_and_count(Path(merged["file_path"]))
    assert merged["columns"] == headers, (
        f"{subdir}: merged_dataset.columns drift from csv header"
    )
    assert merged["row_count"] == row_count, (
        f"{subdir}: merged_dataset.row_count drift from csv body length"
    )


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_core_hypothesis_variable_is_independent_in_spec(subdir: str) -> None:
    state = _load_fixture(subdir)
    independent_names = {
        v["name"]
        for v in state["empirical_spec"]["variables"]
        if v["role"] == "independent"
    }
    core_var = state["model_plan"]["core_hypothesis"]["variable_name"]
    assert core_var in independent_names, (
        f"{subdir}: core_hypothesis.variable_name={core_var!r} not in"
        f" empirical_spec independent vars {independent_names!r}"
    )


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_expected_sign_in_allowed_set(subdir: str) -> None:
    state = _load_fixture(subdir)
    sign = state["model_plan"]["core_hypothesis"]["expected_sign"]
    assert sign in _VALID_EXPECTED_SIGNS, (
        f"{subdir}: expected_sign={sign!r} not in {_VALID_EXPECTED_SIGNS!r}"
    )


@pytest.mark.parametrize("subdir", _FIXTURE_SUBDIRS)
def test_equation_references_core_variable(subdir: str) -> None:
    state = _load_fixture(subdir)
    plan = state["model_plan"]
    core_var = plan["core_hypothesis"]["variable_name"]
    pattern = rf"(?<![A-Za-z0-9]){re.escape(core_var)}(?![A-Za-z0-9])"
    assert re.search(pattern, plan["equation"]), (
        f"{subdir}: equation does not reference core variable {core_var!r}"
    )
