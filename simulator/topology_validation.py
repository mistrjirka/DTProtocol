from __future__ import annotations

import argparse
import json
import math
import random
from collections import deque
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from model import Profile
from simulator import Simulator

Edge = Tuple[int, int]


def canonical_edges(edges: Iterable[Edge]) -> List[Edge]:
    return sorted({(min(a, b), max(a, b)) for a, b in edges if a != b})


def adjacency(n: int, edges: Sequence[Edge]) -> Dict[int, Set[int]]:
    result = {node: set() for node in range(1, n + 1)}
    for a, b in edges:
        result[a].add(b)
        result[b].add(a)
    return result


def connected(n: int, edges: Sequence[Edge]) -> bool:
    if n <= 1:
        return True
    adj = adjacency(n, edges)
    seen = {1}
    queue = deque([1])
    while queue:
        node = queue.popleft()
        for other in adj[node]:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return len(seen) == n


def components(n: int, edges: Sequence[Edge]) -> List[Set[int]]:
    adj = adjacency(n, edges)
    unseen = set(adj)
    result: List[Set[int]] = []
    while unseen:
        root = min(unseen)
        part = {root}
        queue = deque([root])
        unseen.remove(root)
        while queue:
            node = queue.popleft()
            for other in adj[node]:
                if other in unseen:
                    unseen.remove(other)
                    part.add(other)
                    queue.append(other)
        result.append(part)
    return result


def bridge_edges(n: int, edges: Sequence[Edge]) -> List[Edge]:
    base = len(components(n, edges))
    return [
        edge
        for edge in edges
        if len(components(n, [candidate for candidate in edges if candidate != edge])) > base
    ]


def farthest_pair(n: int, edges: Sequence[Edge]) -> Tuple[int, int]:
    adj = adjacency(n, edges)
    best = (1, 1, -1)
    for source in range(1, n + 1):
        distances = {source: 0}
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for other in adj[node]:
                if other not in distances:
                    distances[other] = distances[node] + 1
                    queue.append(other)
        for target, distance in distances.items():
            candidate = (distance, -source, -target)
            if candidate > (best[2], -best[0], -best[1]):
                best = (source, target, distance)
    return best[0], best[1]


def topology_edges(name: str, n: int, seed: int = 1) -> List[Edge]:
    if n < 2:
        return []
    rng = random.Random(seed ^ 0x54_4F_50_4F)
    if name == "line":
        edges = [(node, node + 1) for node in range(1, n)]
    elif name == "ring":
        edges = [(node, node + 1) for node in range(1, n)]
        if n > 2:
            edges.append((1, n))
    elif name == "star":
        edges = [(1, node) for node in range(2, n + 1)]
    elif name == "tree":
        edges = [(rng.randint(1, node - 1), node) for node in range(2, n + 1)]
    elif name == "binary-tree":
        edges = [(node // 2, node) for node in range(2, n + 1)]
    elif name == "wheel":
        if n < 4:
            return topology_edges("complete", n, seed)
        edges = [(1, node) for node in range(2, n + 1)]
        edges += [(node, node + 1) for node in range(2, n)] + [(2, n)]
    elif name == "ladder":
        width = max(2, n // 2)
        top = list(range(1, width + 1))
        bottom = list(range(width + 1, min(n, 2 * width) + 1))
        edges = list(zip(top, top[1:])) + list(zip(bottom, bottom[1:]))
        edges += list(zip(top[: len(bottom)], bottom))
        for node in range(2 * width + 1, n + 1):
            edges.append((node - 1, node))
    elif name == "grid":
        columns = max(2, math.ceil(math.sqrt(n)))
        edges = []
        for node in range(1, n + 1):
            row, column = divmod(node - 1, columns)
            right = node + 1
            down = node + columns
            if right <= n and column + 1 < columns:
                edges.append((node, right))
            if down <= n:
                edges.append((node, down))
    elif name == "barbell":
        if n < 6:
            return topology_edges("line", n, seed)
        left_size = n // 2
        right_start = left_size + 1
        edges = list(combinations(range(1, left_size + 1), 2))
        edges += list(combinations(range(right_start, n + 1), 2))
        edges.append((left_size, right_start))
    elif name == "complete":
        edges = list(combinations(range(1, n + 1), 2))
    elif name.startswith("random-"):
        probability = {
            "random-sparse": 0.12,
            "random-medium": 0.28,
            "random-dense": 0.55,
        }[name]
        # Random spanning tree first so density, not connectivity luck, is varied.
        edges = [(rng.randint(1, node - 1), node) for node in range(2, n + 1)]
        existing = set(canonical_edges(edges))
        for a, b in combinations(range(1, n + 1), 2):
            if (a, b) not in existing and rng.random() < probability:
                edges.append((a, b))
    else:
        raise ValueError(f"unknown topology family {name!r}")
    result = canonical_edges(edges)
    if not connected(n, result):
        raise AssertionError((name, n, result))
    return result


TOPOLOGY_FAMILIES = (
    "line",
    "ring",
    "star",
    "tree",
    "binary-tree",
    "wheel",
    "ladder",
    "grid",
    "barbell",
    "random-sparse",
    "random-medium",
    "random-dense",
)


@dataclass
class PhaseResult:
    converged: bool
    elapsed_ms: int
    sampled_loops: int
    missing: int
    stale: int
    wrong_distance: int


@dataclass
class ValidationResult:
    family: str
    nodes: int
    edges: int
    seed: int
    loss: float
    cut: Optional[Edge]
    initial: PhaseResult
    cut_phase: PhaseResult
    heal: PhaseResult
    reboot: PhaseResult
    data_success: bool
    bytes_on_air: int
    radio_frames: int

    @property
    def correct(self) -> bool:
        return (
            self.initial.converged
            and self.cut_phase.converged
            and self.heal.converged
            and self.reboot.converged
            and self.data_success
            and not any(
                phase.sampled_loops
                for phase in (self.initial, self.cut_phase, self.heal, self.reboot)
            )
        )


def run_until_stable(
    sim: Simulator,
    budget_ms: int,
    *,
    step_ms: int = 10_000,
) -> PhaseResult:
    started = int(sim.now)
    loops = 0
    # Process time-zero startup and any same-timestamp environment events before
    # accepting an audit. Otherwise an entirely unstarted network is vacuously
    # reported as correct because audit() intentionally ignores down nodes.
    sim.run(started)
    audit = sim.audit()
    if audit["correct"]:
        return PhaseResult(True, 0, 0, 0, 0, 0)
    for elapsed in range(step_ms, budget_ms + 1, step_ms):
        sim.run(started + elapsed)
        audit = sim.audit()
        loops += len(audit["loops"])
        if audit["correct"]:
            return PhaseResult(True, elapsed, loops, 0, 0, 0)
    return PhaseResult(
        False,
        budget_ms,
        loops,
        len(audit["missing"]),
        len(audit["stale"]),
        len(audit["wrong_distance"]),
    )


def build_simulator(
    n: int,
    edges: Sequence[Edge],
    *,
    seed: int,
    loss: float,
) -> Simulator:
    sim = Simulator(seed=seed, profile=Profile.crystallized_v2())
    for node in range(1, n + 1):
        sim.add_node(node)
    for a, b in edges:
        sim.add_link(
            a,
            b,
            loss=loss,
            ack_loss=loss,
            latency_ms=0,
            jitter_ms=5,
        )
    return sim


def validate_topology(
    family: str,
    n: int,
    *,
    seed: int = 1,
    loss: float = 0.0,
    convergence_budget_ms: int = 900_000,
) -> ValidationResult:
    edges = topology_edges(family, n, seed)
    sim = build_simulator(n, edges, seed=seed, loss=loss)
    initial = run_until_stable(sim, convergence_budget_ms)

    bridges = bridge_edges(n, edges)
    cut = bridges[0] if bridges else edges[(seed - 1) % len(edges)]
    sim.set_link(*cut, False)
    cut_phase = run_until_stable(sim, convergence_budget_ms)

    sim.set_link(*cut, True)
    heal = run_until_stable(sim, convergence_budget_ms)

    reboot_node = 1 + (seed % n)
    reboot_downtime_ms = 500
    sim.reboot_node(reboot_node, downtime_ms=reboot_downtime_ms)
    sim.run(sim.now + reboot_downtime_ms)
    reboot = run_until_stable(sim, convergence_budget_ms)

    source, target = farthest_pair(n, edges)
    before_success = sim.metrics.e2e_success
    data_success = False
    # One application transaction is allowed to fail under a stochastic link
    # model. Test eventual delivery with fresh message identities rather than
    # accidentally treating a sampled erasure as a routing failure.
    for _ in range(5):
        packet_id = sim.nodes[source].send_data(
            target,
            payload_size=24,
            e2e_ack=True,
            timeout_ms=300_000,
        )
        sim.run(sim.now + 360_000)
        if packet_id is not None and sim.metrics.e2e_success > before_success:
            data_success = True
            break

    return ValidationResult(
        family=family,
        nodes=n,
        edges=len(edges),
        seed=seed,
        loss=loss,
        cut=cut,
        initial=initial,
        cut_phase=cut_phase,
        heal=heal,
        reboot=reboot,
        data_success=data_success,
        bytes_on_air=sim.metrics.bytes_on_air,
        radio_frames=(
            sim.metrics.radio_data_frames + sim.metrics.radio_link_ack_frames
        ),
    )


def enumerate_connected_graphs(n: int) -> Iterable[List[Edge]]:
    possible = list(combinations(range(1, n + 1), 2))
    for mask in range(1, 1 << len(possible)):
        edges = [
            possible[index]
            for index in range(len(possible))
            if (mask >> index) & 1
        ]
        if connected(n, edges):
            yield edges


def validate_exhaustive(
    n: int,
    *,
    budget_ms: int = 600_000,
    max_graphs: Optional[int] = None,
) -> dict:
    graphs = cases = 0
    failures = []
    for graph_index, edges in enumerate(enumerate_connected_graphs(n), start=1):
        if max_graphs is not None and graphs >= max_graphs:
            break
        graphs += 1
        seed = 70_000 + graph_index
        sim = build_simulator(n, edges, seed=seed, loss=0.0)
        initial = run_until_stable(sim, budget_ms)
        cases += 1
        if not initial.converged or initial.sampled_loops:
            failures.append({"graph": graph_index, "phase": "initial", "result": asdict(initial)})
            break

        for edge_index, edge in enumerate(edges):
            sim = build_simulator(
                n,
                edges,
                seed=seed + edge_index + 1,
                loss=0.0,
            )
            if not run_until_stable(sim, budget_ms).converged:
                failures.append({"graph": graph_index, "phase": "pre-cut", "edge": edge})
                break
            sim.set_link(*edge, False)
            down = run_until_stable(sim, budget_ms)
            cases += 1
            if not down.converged or down.sampled_loops:
                failures.append({"graph": graph_index, "phase": "cut", "edge": edge, "result": asdict(down)})
                break
            sim.set_link(*edge, True)
            healed = run_until_stable(sim, budget_ms)
            cases += 1
            if not healed.converged or healed.sampled_loops:
                failures.append({"graph": graph_index, "phase": "heal", "edge": edge, "result": asdict(healed)})
                break
        if failures:
            break
    return {
        "nodes": n,
        "graphs": graphs,
        "cases": cases,
        "failures": failures,
        "correct": not failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=16)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--loss", type=float, default=0.05)
    parser.add_argument("--families", nargs="*", default=list(TOPOLOGY_FAMILIES))
    parser.add_argument("--exhaustive", type=int)
    parser.add_argument("--max-graphs", type=int)
    args = parser.parse_args()

    if args.exhaustive:
        result = validate_exhaustive(
            args.exhaustive,
            max_graphs=args.max_graphs,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["correct"] else 1)

    rows = []
    for family in args.families:
        for seed in range(1, args.runs + 1):
            result = validate_topology(
                family,
                args.nodes,
                seed=seed,
                loss=args.loss,
            )
            rows.append({**asdict(result), "correct": result.correct})
    summary = {
        "nodes": args.nodes,
        "loss": args.loss,
        "runs": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "failures": [row for row in rows if not row["correct"]],
        "rows": rows,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(0 if not summary["failures"] else 1)


if __name__ == "__main__":
    main()
