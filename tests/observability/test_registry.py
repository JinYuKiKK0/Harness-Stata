"""Pure-logic tests for the node-run registry."""

from __future__ import annotations

from harness_stata.observability.registry import NODE_REGISTRY, REQUIRED_FIELDS


def test_node_registry_includes_stata_nodes() -> None:
    assert "descriptive_stats" in NODE_REGISTRY
    assert "regression" in NODE_REGISTRY
    assert callable(NODE_REGISTRY["descriptive_stats"])
    assert callable(NODE_REGISTRY["regression"])


def test_required_fields_descriptive_stats() -> None:
    assert REQUIRED_FIELDS["descriptive_stats"] == ("empirical_spec", "merged_dataset")


def test_required_fields_regression() -> None:
    assert REQUIRED_FIELDS["regression"] == (
        "empirical_spec",
        "merged_dataset",
        "model_plan",
    )


def test_registry_keys_match_required_fields_keys() -> None:
    assert set(NODE_REGISTRY) == set(REQUIRED_FIELDS)
