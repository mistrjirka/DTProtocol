import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pulp = pytest.importorskip("pulp")

from reliability_oracle import (
    OpportunityType,
    independent_attempts_required,
    solve_probabilistic_mobile_schedule,
)


def test_independent_attempt_requirement():
    assert independent_attempts_required(0.1, 1e-4) == 4
    assert independent_attempts_required(0.1, 1e-6) == 6
    assert independent_attempts_required(0.0, 1e-12) == 1


def test_one_second_schedule_is_optimal_for_four_nines_at_ten_percent_failure():
    result = solve_probabilistic_mobile_schedule(
        horizon_slots=60,
        contact_slots=5,
        handshake_slots=1,
        opportunity_types=[OpportunityType("one-radio", 0.1)],
        target_failure_probability=1e-4,
    )
    # Four useful one-second slots and four required independent opportunities:
    # every slot must contain one opportunity.
    assert result.attempts == 60
    assert result.worst_window_failure_bound <= 1.0000001e-4


def test_six_nines_is_impossible_with_only_four_one_radio_opportunities():
    with pytest.raises(ValueError, match="infeasible"):
        solve_probabilistic_mobile_schedule(
            horizon_slots=60,
            contact_slots=5,
            handshake_slots=1,
            opportunity_types=[OpportunityType("one-radio", 0.1)],
            target_failure_probability=1e-6,
        )


def test_half_second_slots_make_six_nines_feasible_without_parallel_radios():
    result = solve_probabilistic_mobile_schedule(
        horizon_slots=120,
        contact_slots=10,
        handshake_slots=2,
        opportunity_types=[OpportunityType("one-radio", 0.1)],
        target_failure_probability=1e-6,
    )
    assert result.worst_window_failure_bound <= 1.000001e-6
    assert result.attempts < 120
