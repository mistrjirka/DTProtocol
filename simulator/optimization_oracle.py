#!/usr/bin/env python3
"""Small MILP oracles for DTProtocol design questions.

This module deliberately does *not* try to synthesize an unbounded asynchronous
routing protocol.  Instead it solves bounded optimization problems that are
useful as exact lower bounds and counterexample generators:

* a cyclic discovery-beacon schedule that guarantees a number of opportunities
  in every contact window; and
* a minimum-airtime synchronous broadcast schedule that disseminates every
  node's state over a fixed small topology.

The models use PuLP/CBC.  They are research tools, not runtime dependencies of
DTProtocol or the normal simulator.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Sequence

try:
    import pulp
except ImportError:  # pragma: no cover - exercised by the friendly CLI error
    pulp = None


ChannelModel = Literal["none", "local", "global"]


class OracleUnavailable(RuntimeError):
    """Raised when the optional MILP dependency is not installed."""


def _require_pulp():
    if pulp is None:
        raise OracleUnavailable(
            "optimization_oracle requires PuLP; install with "
            "`python -m pip install -r simulator/requirements-optimization.txt`"
        )
    return pulp


@dataclass(frozen=True)
class MobileScheduleResult:
    status: str
    horizon_slots: int
    contact_slots: int
    handshake_slots: int
    effective_opportunity_slots: int
    loss_budget: int
    required_opportunities: int
    transmission_slots: tuple[int, ...]
    transmission_count: int
    maximum_cyclic_gap_slots: int

    def validates(self) -> bool:
        if self.status != "Optimal":
            return False
        selected = set(self.transmission_slots)
        for start in range(self.horizon_slots):
            count = sum(
                (start + offset) % self.horizon_slots in selected
                for offset in range(self.effective_opportunity_slots)
            )
            if count < self.required_opportunities:
                return False
        return True


@dataclass(frozen=True)
class DisseminationResult:
    status: str
    nodes: int
    edges: tuple[tuple[int, int], ...]
    rounds: int
    channel_model: ChannelModel
    transmission_schedule: tuple[tuple[int, ...], ...]
    transmission_count: int
    objective_bytes: int
    base_frame_bytes: int
    record_bytes: int
    final_knowledge: tuple[tuple[bool, ...], ...]

    @property
    def complete(self) -> bool:
        return self.status == "Optimal" and all(
            all(row) for row in self.final_knowledge
        )


def _solve(problem, *, solver_msg: bool = False) -> str:
    pl = _require_pulp()
    solver = pl.PULP_CBC_CMD(msg=solver_msg)
    problem.solve(solver)
    return pl.LpStatus[problem.status]


def solve_mobile_schedule(
    *,
    horizon_slots: int,
    contact_slots: int,
    handshake_slots: int = 0,
    loss_budget: int = 0,
    solver_msg: bool = False,
) -> MobileScheduleResult:
    """Find the sparsest repeating beacon schedule with a sliding-window bound.

    A contact may begin at any phase in the repeating ``horizon_slots`` cycle.
    ``handshake_slots`` are reserved at the end of the contact for the reliable
    request/response exchange, leaving ``contact_slots - handshake_slots`` slots
    in which a discovery beacon may start.  ``loss_budget`` means that many
    complete discovery opportunities may be erased adversarially, so every
    effective contact window must contain at least ``loss_budget + 1`` beacons.

    This is a deterministic bounded-loss guarantee.  Independent random loss
    has no literal 100% guarantee; the result instead lets us choose enough
    opportunities to make its residual probability as small as desired.
    """

    pl = _require_pulp()
    if horizon_slots <= 0 or contact_slots <= 0:
        raise ValueError("horizon_slots and contact_slots must be positive")
    if handshake_slots < 0 or loss_budget < 0:
        raise ValueError("handshake_slots and loss_budget must be non-negative")
    effective = contact_slots - handshake_slots
    if effective <= 0:
        raise ValueError("contact must leave at least one beacon-opportunity slot")
    required = loss_budget + 1
    if required > effective:
        raise ValueError(
            "loss budget requires more opportunities than fit in one contact window"
        )

    problem = pl.LpProblem("dtprotocol_mobile_discovery", pl.LpMinimize)
    tx = {
        slot: pl.LpVariable(f"tx_{slot}", cat=pl.LpBinary)
        for slot in range(horizon_slots)
    }
    problem += pl.lpSum(tx.values())

    for start in range(horizon_slots):
        problem += (
            pl.lpSum(
                tx[(start + offset) % horizon_slots]
                for offset in range(effective)
            )
            >= required,
            f"contact_start_{start}",
        )

    # Cyclic shifts are equivalent.  Pin one selected slot to remove symmetry.
    problem += tx[0] == 1, "cyclic_symmetry"
    status = _solve(problem, solver_msg=solver_msg)
    selected = tuple(
        slot
        for slot in range(horizon_slots)
        if status == "Optimal" and pl.value(tx[slot]) > 0.5
    )
    if selected:
        gaps = [
            (selected[(index + 1) % len(selected)] - selected[index])
            % horizon_slots
            for index in range(len(selected))
        ]
        maximum_gap = max(gaps)
    else:
        maximum_gap = horizon_slots

    result = MobileScheduleResult(
        status=status,
        horizon_slots=horizon_slots,
        contact_slots=contact_slots,
        handshake_slots=handshake_slots,
        effective_opportunity_slots=effective,
        loss_budget=loss_budget,
        required_opportunities=required,
        transmission_slots=selected,
        transmission_count=len(selected),
        maximum_cyclic_gap_slots=maximum_gap,
    )
    if status == "Optimal" and not result.validates():
        raise AssertionError("MILP returned a schedule that violates its own model")
    return result


def _normalize_edges(
    nodes: int, edges: Iterable[tuple[int, int]]
) -> tuple[tuple[int, int], ...]:
    if nodes <= 0:
        raise ValueError("nodes must be positive")
    normalized: set[tuple[int, int]] = set()
    for raw_a, raw_b in edges:
        a, b = int(raw_a), int(raw_b)
        if a == b:
            raise ValueError("self edges are not supported")
        if not (0 <= a < nodes and 0 <= b < nodes):
            raise ValueError(f"edge {(a, b)} is outside 0..{nodes - 1}")
        normalized.add((min(a, b), max(a, b)))
    return tuple(sorted(normalized))


def solve_dissemination(
    *,
    nodes: int,
    edges: Iterable[tuple[int, int]],
    rounds: int,
    channel_model: ChannelModel = "global",
    base_frame_bytes: int = 22,
    record_bytes: int = 5,
    solver_msg: bool = False,
) -> DisseminationResult:
    """Compute a minimum-byte state-dissemination schedule for a small graph.

    The model is synchronous and lossless.  Every node initially knows only its
    own state.  A broadcast carries the sender identity in the fixed header and
    one ``record_bytes`` record for each *other* state currently known by the
    sender.  Knowledge is monotonic and broadcasts are heard by all neighbours.

    ``channel_model`` controls concurrency:

    * ``none``: only half-duplex is enforced;
    * ``local``: at most one neighbour of each receiver may transmit per round;
    * ``global``: at most one node transmits in the entire network per round.

    This is an optimistic lower bound for the asynchronous LoRa protocol: it has
    a globally chosen schedule, no packet loss, no control handshake and no
    chunk overhead beyond the linear byte objective.
    """

    pl = _require_pulp()
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    if channel_model not in ("none", "local", "global"):
        raise ValueError(f"unknown channel model {channel_model!r}")
    if base_frame_bytes <= 0 or record_bytes < 0:
        raise ValueError("frame byte costs must be non-negative and base > 0")

    edge_tuple = _normalize_edges(nodes, edges)
    neighbours: dict[int, set[int]] = {node: set() for node in range(nodes)}
    for a, b in edge_tuple:
        neighbours[a].add(b)
        neighbours[b].add(a)

    problem = pl.LpProblem("dtprotocol_state_dissemination", pl.LpMinimize)
    tx = {
        (node, round_index): pl.LpVariable(
            f"tx_{node}_{round_index}", cat=pl.LpBinary
        )
        for node in range(nodes)
        for round_index in range(rounds)
    }
    known = {
        (node, datum, round_index): pl.LpVariable(
            f"known_{node}_{datum}_{round_index}", cat=pl.LpBinary
        )
        for node in range(nodes)
        for datum in range(nodes)
        for round_index in range(rounds + 1)
    }
    carries = {
        (node, datum, round_index): pl.LpVariable(
            f"carries_{node}_{datum}_{round_index}", cat=pl.LpBinary
        )
        for node in range(nodes)
        for datum in range(nodes)
        for round_index in range(rounds)
    }
    transfer = {
        (sender, receiver, datum, round_index): pl.LpVariable(
            f"transfer_{sender}_{receiver}_{datum}_{round_index}",
            cat=pl.LpBinary,
        )
        for sender in range(nodes)
        for receiver in neighbours[sender]
        for datum in range(nodes)
        for round_index in range(rounds)
    }

    for node in range(nodes):
        for datum in range(nodes):
            problem += known[node, datum, 0] == int(node == datum)

    for round_index in range(rounds):
        if channel_model == "global":
            problem += (
                pl.lpSum(tx[node, round_index] for node in range(nodes)) <= 1,
                f"global_channel_{round_index}",
            )
        elif channel_model == "local":
            for receiver in range(nodes):
                problem += (
                    pl.lpSum(
                        tx[sender, round_index]
                        for sender in neighbours[receiver]
                    )
                    <= 1,
                    f"local_channel_{receiver}_{round_index}",
                )

        for node in range(nodes):
            for datum in range(nodes):
                z = carries[node, datum, round_index]
                problem += z <= tx[node, round_index]
                problem += z <= known[node, datum, round_index]
                problem += (
                    z >= tx[node, round_index] + known[node, datum, round_index] - 1
                )

        for sender in range(nodes):
            for receiver in neighbours[sender]:
                for datum in range(nodes):
                    y = transfer[sender, receiver, datum, round_index]
                    problem += y <= tx[sender, round_index]
                    problem += y <= known[sender, datum, round_index]
                    problem += y <= 1 - tx[receiver, round_index]
                    problem += (
                        y
                        >= tx[sender, round_index]
                        + known[sender, datum, round_index]
                        - tx[receiver, round_index]
                        - 1
                    )

        for receiver in range(nodes):
            for datum in range(nodes):
                next_known = known[receiver, datum, round_index + 1]
                previous_known = known[receiver, datum, round_index]
                incoming = [
                    transfer[sender, receiver, datum, round_index]
                    for sender in neighbours[receiver]
                ]
                problem += next_known >= previous_known
                for item in incoming:
                    problem += next_known >= item
                problem += next_known <= previous_known + pl.lpSum(incoming)

    for node in range(nodes):
        for datum in range(nodes):
            problem += known[node, datum, rounds] == 1

    byte_terms = []
    for node in range(nodes):
        for round_index in range(rounds):
            byte_terms.append(base_frame_bytes * tx[node, round_index])
            byte_terms.extend(
                record_bytes * carries[node, datum, round_index]
                for datum in range(nodes)
                if datum != node
            )
    problem += pl.lpSum(byte_terms)

    status = _solve(problem, solver_msg=solver_msg)
    schedule = tuple(
        tuple(
            node
            for node in range(nodes)
            if status == "Optimal" and pl.value(tx[node, round_index]) > 0.5
        )
        for round_index in range(rounds)
    )
    final_knowledge = tuple(
        tuple(
            bool(status == "Optimal" and pl.value(known[node, datum, rounds]) > 0.5)
            for datum in range(nodes)
        )
        for node in range(nodes)
    )
    objective = (
        int(round(pl.value(problem.objective))) if status == "Optimal" else 0
    )
    return DisseminationResult(
        status=status,
        nodes=nodes,
        edges=edge_tuple,
        rounds=rounds,
        channel_model=channel_model,
        transmission_schedule=schedule,
        transmission_count=sum(len(item) for item in schedule),
        objective_bytes=objective,
        base_frame_bytes=base_frame_bytes,
        record_bytes=record_bytes,
        final_knowledge=final_knowledge,
    )


def topology_edges(kind: str, nodes: int) -> tuple[tuple[int, int], ...]:
    if nodes <= 0:
        raise ValueError("nodes must be positive")
    if kind == "line":
        return tuple((node, node + 1) for node in range(nodes - 1))
    if kind == "ring":
        edges = list(topology_edges("line", nodes))
        if nodes > 2:
            edges.append((0, nodes - 1))
        return tuple(edges)
    if kind == "star":
        return tuple((0, node) for node in range(1, nodes))
    if kind == "complete":
        return tuple(
            (a, b) for a in range(nodes) for b in range(a + 1, nodes)
        )
    raise ValueError(f"unknown topology {kind!r}")


def _parse_edges(text: str) -> tuple[tuple[int, int], ...]:
    if not text.strip():
        return ()
    result = []
    for item in text.split(","):
        left, right = item.strip().split("-", 1)
        result.append((int(left), int(right)))
    return tuple(result)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mobile = sub.add_parser("mobile", help="optimize a cyclic discovery schedule")
    mobile.add_argument("--horizon-slots", type=int, default=60)
    mobile.add_argument("--contact-slots", type=int, default=5)
    mobile.add_argument("--handshake-slots", type=int, default=1)
    mobile.add_argument("--loss-budget", type=int, default=0)
    mobile.add_argument("--solver-msg", action="store_true")

    dissemination = sub.add_parser(
        "dissemination", help="optimize small-topology state dissemination"
    )
    dissemination.add_argument("--nodes", type=int, default=5)
    dissemination.add_argument(
        "--topology", choices=("line", "ring", "star", "complete"), default="line"
    )
    dissemination.add_argument(
        "--edges", default="", help="override topology, e.g. 0-1,1-2,0-2"
    )
    dissemination.add_argument("--rounds", type=int, default=8)
    dissemination.add_argument(
        "--channel-model", choices=("none", "local", "global"), default="global"
    )
    dissemination.add_argument("--base-frame-bytes", type=int, default=22)
    dissemination.add_argument("--record-bytes", type=int, default=5)
    dissemination.add_argument("--solver-msg", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "mobile":
            result = solve_mobile_schedule(
                horizon_slots=args.horizon_slots,
                contact_slots=args.contact_slots,
                handshake_slots=args.handshake_slots,
                loss_budget=args.loss_budget,
                solver_msg=args.solver_msg,
            )
        else:
            edges = (
                _parse_edges(args.edges)
                if args.edges
                else topology_edges(args.topology, args.nodes)
            )
            result = solve_dissemination(
                nodes=args.nodes,
                edges=edges,
                rounds=args.rounds,
                channel_model=args.channel_model,
                base_frame_bytes=args.base_frame_bytes,
                record_bytes=args.record_bytes,
                solver_msg=args.solver_msg,
            )
    except (OracleUnavailable, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(asdict(result), indent=2))
    return 0 if result.status == "Optimal" else 2


if __name__ == "__main__":
    raise SystemExit(main())
