from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from typing import Iterable, List, Sequence, Tuple

import pulp


@dataclass(frozen=True)
class OpportunityType:
    name: str
    failure_probability: float
    cost: float = 1.0


@dataclass
class ReliabilitySchedule:
    horizon_slots: int
    contact_slots: int
    handshake_slots: int
    target_failure_probability: float
    objective_cost: float
    schedule: List[Tuple[int, str]]
    attempts: int
    guaranteed_log_reliability: float
    worst_window_failure_bound: float
    status: str


def information_weight(failure_probability: float, target: float) -> float:
    if not 0.0 <= failure_probability <= 1.0:
        raise ValueError("failure probability must be in [0, 1]")
    if failure_probability == 0.0:
        # One deterministic opportunity is sufficient for any finite target.
        return -math.log(target)
    if failure_probability == 1.0:
        return 0.0
    return -math.log(failure_probability)


def independent_attempts_required(
    failure_probability: float,
    target_failure_probability: float,
) -> int:
    if not 0.0 < target_failure_probability < 1.0:
        raise ValueError("target failure probability must be in (0, 1)")
    if failure_probability == 0.0:
        return 1
    if not 0.0 < failure_probability < 1.0:
        raise ValueError("per-attempt failure probability must be in [0, 1)")
    return math.ceil(
        math.log(target_failure_probability) / math.log(failure_probability)
    )


def cyclic_window(start: int, length: int, horizon: int) -> List[int]:
    return [(start + offset) % horizon for offset in range(length)]


def solve_probabilistic_mobile_schedule(
    *,
    horizon_slots: int,
    contact_slots: int,
    handshake_slots: int,
    opportunity_types: Sequence[OpportunityType],
    target_failure_probability: float,
    max_attempts_per_slot: int = 1,
) -> ReliabilitySchedule:
    """Minimum-cost cyclic discovery schedule under independent erasures.

    Every arbitrary contact phase must contain enough complete discovery
    opportunities before the handshake guard interval.  Taking logarithms turns
    the product-of-failure-probabilities requirement into a linear constraint.

    This is an oracle for a *specified physical model*, not unrestricted
    protocol synthesis. Independence, slot duration and handshake duration are
    explicit assumptions and must be validated against the radio simulator.
    """
    if horizon_slots <= 0:
        raise ValueError("horizon_slots must be positive")
    if not 0 <= handshake_slots < contact_slots <= horizon_slots:
        raise ValueError("require 0 <= handshake < contact <= horizon")
    if not opportunity_types:
        raise ValueError("at least one opportunity type is required")
    if max_attempts_per_slot <= 0:
        raise ValueError("max_attempts_per_slot must be positive")
    if not 0.0 < target_failure_probability < 1.0:
        raise ValueError("target failure probability must be in (0, 1)")

    useful_slots = contact_slots - handshake_slots
    required_weight = -math.log(target_failure_probability)
    weights = {
        option.name: information_weight(
            option.failure_probability, target_failure_probability
        )
        for option in opportunity_types
    }

    problem = pulp.LpProblem("mobile_reliability_schedule", pulp.LpMinimize)
    variables = {
        (slot, option.name): pulp.LpVariable(
            f"x_{slot}_{option.name}", cat="Binary"
        )
        for slot in range(horizon_slots)
        for option in opportunity_types
    }
    problem += pulp.lpSum(
        variables[slot, option.name] * option.cost
        for slot in range(horizon_slots)
        for option in opportunity_types
    )

    for slot in range(horizon_slots):
        problem += (
            pulp.lpSum(
                variables[slot, option.name]
                for option in opportunity_types
            )
            <= max_attempts_per_slot
        )

    for start in range(horizon_slots):
        valid = cyclic_window(start, useful_slots, horizon_slots)
        problem += (
            pulp.lpSum(
                variables[slot, option.name] * weights[option.name]
                for slot in valid
                for option in opportunity_types
            )
            >= required_weight
        )

    status_code = problem.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[status_code]
    if status != "Optimal":
        raise ValueError(
            "requested confidence is infeasible under the supplied slot, "
            f"handshake, radio and attempt constraints (solver={status})"
        )

    schedule = sorted(
        (slot, option.name)
        for slot in range(horizon_slots)
        for option in opportunity_types
        if pulp.value(variables[slot, option.name]) > 0.5
    )
    selected = set(schedule)
    worst_weight = float("inf")
    worst_failure = 0.0
    option_by_name = {option.name: option for option in opportunity_types}
    for start in range(horizon_slots):
        valid = set(cyclic_window(start, useful_slots, horizon_slots))
        window_weight = sum(
            weights[name]
            for slot, name in schedule
            if slot in valid
        )
        failure = 1.0
        for slot, name in schedule:
            if slot in valid:
                failure *= option_by_name[name].failure_probability
        worst_weight = min(worst_weight, window_weight)
        worst_failure = max(worst_failure, failure)

    return ReliabilitySchedule(
        horizon_slots=horizon_slots,
        contact_slots=contact_slots,
        handshake_slots=handshake_slots,
        target_failure_probability=target_failure_probability,
        objective_cost=float(pulp.value(problem.objective)),
        schedule=schedule,
        attempts=len(schedule),
        guaranteed_log_reliability=worst_weight,
        worst_window_failure_bound=worst_failure,
        status=status,
    )


def pareto_frontier(
    *,
    horizon_slots: int,
    contact_slots: int,
    handshake_slots: int,
    failure_probability: float,
    targets: Iterable[float],
) -> List[dict]:
    rows = []
    option = OpportunityType("beacon-handshake", failure_probability, 1.0)
    for target in targets:
        try:
            result = solve_probabilistic_mobile_schedule(
                horizon_slots=horizon_slots,
                contact_slots=contact_slots,
                handshake_slots=handshake_slots,
                opportunity_types=[option],
                target_failure_probability=target,
            )
            rows.append({"target": target, "feasible": True, **asdict(result)})
        except ValueError as error:
            rows.append({"target": target, "feasible": False, "error": str(error)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon-slots", type=int, default=60)
    parser.add_argument("--contact-slots", type=int, default=5)
    parser.add_argument("--handshake-slots", type=int, default=1)
    parser.add_argument("--failure", type=float, default=0.1)
    parser.add_argument(
        "--targets",
        type=float,
        nargs="+",
        default=[1e-2, 1e-3, 1e-4, 1e-6],
    )
    args = parser.parse_args()
    result = pareto_frontier(
        horizon_slots=args.horizon_slots,
        contact_slots=args.contact_slots,
        handshake_slots=args.handshake_slots,
        failure_probability=args.failure,
        targets=args.targets,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
