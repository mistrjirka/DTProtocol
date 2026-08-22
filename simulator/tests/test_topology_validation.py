import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from topology_validation import (
    TOPOLOGY_FAMILIES,
    connected,
    topology_edges,
    validate_exhaustive,
    validate_topology,
)


def test_all_named_topology_families_are_connected():
    for family in TOPOLOGY_FAMILIES:
        edges = topology_edges(family, 10, seed=11)
        assert connected(10, edges), family


def test_representative_topologies_survive_cut_heal_reboot_and_data():
    for family in ("line", "ring", "star", "grid", "barbell", "random-medium"):
        result = validate_topology(
            family,
            8,
            seed=12,
            loss=0.0,
            convergence_budget_ms=500_000,
        )
        assert result.correct, result


def test_all_connected_four_node_graphs_and_single_edge_events():
    result = validate_exhaustive(4, budget_ms=400_000)
    assert result["graphs"] == 38
    assert result["correct"], result
