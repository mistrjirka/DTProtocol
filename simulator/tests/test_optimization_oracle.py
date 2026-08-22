import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

pytest.importorskip("pulp")

from optimization_oracle import (
    solve_dissemination,
    solve_mobile_schedule,
    topology_edges,
)


def test_mobile_oracle_finds_sparse_five_second_lossless_schedule():
    result = solve_mobile_schedule(
        horizon_slots=20,
        contact_slots=5,
        handshake_slots=1,
        loss_budget=0,
    )
    assert result.status == "Optimal"
    assert result.validates()
    assert result.transmission_count == 5
    assert result.maximum_cyclic_gap_slots <= 4


def test_mobile_oracle_adds_redundancy_for_one_adversarial_loss():
    result = solve_mobile_schedule(
        horizon_slots=20,
        contact_slots=5,
        handshake_slots=1,
        loss_budget=1,
    )
    assert result.status == "Optimal"
    assert result.validates()
    assert result.transmission_count == 10


def test_three_node_line_requires_three_global_broadcasts():
    result = solve_dissemination(
        nodes=3,
        edges=topology_edges("line", 3),
        rounds=3,
        channel_model="global",
    )
    assert result.status == "Optimal"
    assert result.complete
    assert result.transmission_count == 3


def test_four_node_star_lower_bound_is_each_leaf_then_center():
    result = solve_dissemination(
        nodes=4,
        edges=topology_edges("star", 4),
        rounds=4,
        channel_model="global",
    )
    assert result.status == "Optimal"
    assert result.complete
    assert result.transmission_count == 4


def test_too_few_rounds_is_infeasible():
    result = solve_dissemination(
        nodes=3,
        edges=topology_edges("line", 3),
        rounds=2,
        channel_model="global",
    )
    assert result.status == "Infeasible"
    assert not result.complete
