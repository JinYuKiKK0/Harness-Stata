"""Pure-logic tests for ``FixtureLoader``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_stata.observability.loader import FixtureLoader
from harness_stata.observability.store import DEFAULT_HARNESS_DIR, LATEST_FILE_NAME


def _make_run_input(project_root: Path, run_id: str, node: str, state: dict) -> Path:
    """Materialize a NodeIOPayload-shaped input.json under runs/<id>/."""
    target_dir = project_root / DEFAULT_HARNESS_DIR / "runs" / run_id / "nodes" / node
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "namespace": [],
        "node": node,
        "kind": "input",
        "state": state,
    }
    (target_dir / "input.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return target_dir / "input.json"


def _make_fixture(project_root: Path, subdir: str, state: dict) -> Path:
    """Materialize a plain WorkflowState input_state.json under downloads/fixtures/."""
    target_dir = project_root / "downloads" / "fixtures" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "input_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    return target_dir / "input_state.json"


# ---------------------------------------------------------------------------
# load_from_run
# ---------------------------------------------------------------------------


class TestLoadFromRun:
    def test_unwraps_state_field(self, tmp_path: Path) -> None:
        cleaning_state = {
            "downloaded_files": {"files": [{"path": "x.csv"}]},
            "empirical_spec": {"topic": "x", "variables": []},
        }
        _make_run_input(tmp_path, "20260502T103500Z-aaaa", "data_cleaning", cleaning_state)

        loader = FixtureLoader(tmp_path)
        state, source = loader.load_from_run("20260502T103500Z-aaaa", "data_cleaning")

        assert state["downloaded_files"]["files"][0]["path"] == "x.csv"
        assert source == "runs/20260502T103500Z-aaaa/data_cleaning"

    def test_missing_input_json(self, tmp_path: Path) -> None:
        loader = FixtureLoader(tmp_path)
        with pytest.raises(FileNotFoundError):
            loader.load_from_run("nonexistent-run", "data_cleaning")

    def test_malformed_payload_raises(self, tmp_path: Path) -> None:
        target = (
            tmp_path
            / DEFAULT_HARNESS_DIR
            / "runs"
            / "r1"
            / "nodes"
            / "data_cleaning"
            / "input.json"
        )
        target.parent.mkdir(parents=True)
        # Write top-level object without 'state' key
        target.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

        loader = FixtureLoader(tmp_path)
        with pytest.raises(ValueError, match="missing or non-dict 'state'"):
            loader.load_from_run("r1", "data_cleaning")


# ---------------------------------------------------------------------------
# load_from_fixture
# ---------------------------------------------------------------------------


class TestLoadFromFixture:
    def test_loads_plain_state(self, tmp_path: Path) -> None:
        plain_state = {
            "downloaded_files": {"files": [{"path": "y.csv"}]},
            "empirical_spec": {"topic": "y"},
        }
        _make_fixture(tmp_path, "01_capital", plain_state)

        loader = FixtureLoader(tmp_path)
        state, source = loader.load_from_fixture("01_capital", "data_cleaning")

        assert state["empirical_spec"]["topic"] == "y"
        assert source == "fixtures/01_capital"

    def test_missing_fixture(self, tmp_path: Path) -> None:
        loader = FixtureLoader(tmp_path)
        with pytest.raises(FileNotFoundError):
            loader.load_from_fixture("does-not-exist", "data_cleaning")


# ---------------------------------------------------------------------------
# load_latest
# ---------------------------------------------------------------------------


class TestLoadLatest:
    def test_resolves_through_pointer(self, tmp_path: Path) -> None:
        _make_run_input(
            tmp_path,
            "20260502T103500Z-bbbb",
            "data_cleaning",
            {
                "downloaded_files": {"files": [{"path": "z.csv"}]},
                "empirical_spec": {"topic": "z"},
            },
        )
        latest = tmp_path / DEFAULT_HARNESS_DIR / LATEST_FILE_NAME
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text("20260502T103500Z-bbbb", encoding="utf-8")

        loader = FixtureLoader(tmp_path)
        state, source = loader.load_latest("data_cleaning")
        assert state["empirical_spec"]["topic"] == "z"
        assert source.startswith("runs/20260502T103500Z-bbbb/")

    def test_no_pointer_raises(self, tmp_path: Path) -> None:
        loader = FixtureLoader(tmp_path)
        with pytest.raises(FileNotFoundError, match="latest"):
            loader.load_latest("data_cleaning")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_unknown_node_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown node"):
            FixtureLoader.validate_for_node({}, "no_such_node")

    def test_missing_field_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing fields"):
            FixtureLoader.validate_for_node({}, "data_cleaning")

    def test_data_probe_only_needs_empirical_spec(self) -> None:
        FixtureLoader.validate_for_node({"empirical_spec": {"x": 1}}, "data_probe")

    def test_data_cleaning_needs_both(self) -> None:
        with pytest.raises(ValueError, match="missing fields"):
            FixtureLoader.validate_for_node({"empirical_spec": {"x": 1}}, "data_cleaning")
