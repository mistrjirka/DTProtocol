from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import deque
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Set, Tuple

from scenario import LinkSpec, NodeSpec, Scenario
from topology_validation import Edge, canonical_edges, topology_edges


def shortest_paths(n: int, edges: Sequence[Edge]) -> Dict[int, Dict[int, int]]:
    adjacency = {node: set() for node in range(1, n + 1)}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    result = {}
    for source in range(1, n + 1):
        distances = {source: 0}
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for other in adjacency[node]:
                if other not in distances:
                    distances[other] = distances[node] + 1
                    queue.append(other)
        result[source] = distances
    return result


def audit(network, n: int, edges: Sequence[Edge]) -> dict:
    truth = shortest_paths(n, edges)
    links = {frozenset(edge) for edge in edges}
    missing = []
    stale = []
    wrong = []
    loops = []
    snapshots = {node: network.routes(node) for node in range(1, n + 1)}
    for source in range(1, n + 1):
        for destination, distance in truth[source].items():
            if destination == source:
                continue
            route = snapshots[source].get(destination)
            if route is None:
                missing.append((source, destination, distance))
                continue
            if route[1] != distance:
                wrong.append((source, destination, route[1], distance))
            path = [source]
            current = source
            for _ in range(n + 1):
                if current == destination:
                    break
                current_route = snapshots[current].get(destination)
                if current_route is None:
                    break
                next_hop = current_route[0]
                if frozenset((current, next_hop)) not in links:
                    break
                if next_hop in path:
                    path.append(next_hop)
                    loops.append((source, destination, path))
                    break
                path.append(next_hop)
                current = next_hop
        for destination in snapshots[source]:
            if destination not in truth[source]:
                stale.append((source, destination))
    return {
        "correct": not (missing or stale or wrong or loops),
        "missing": missing,
        "stale": stale,
        "wrong": wrong,
        "loops": loops,
    }


def run_until_correct(network, n: int, edges: Sequence[Edge], budget_ms: int, step_ms: int = 100_000):
    started = int(network.now)
    sampled_loops = 0
    last = audit(network, n, edges)
    if last["correct"]:
        return 0, sampled_loops, last
    for elapsed in range(step_ms, budget_ms + 1, step_ms):
        network.run(started + elapsed)
        last = audit(network, n, edges)
        sampled_loops += len(last["loops"])
        if last["correct"]:
            return elapsed, sampled_loops, last
    return None, sampled_loops, last


@dataclass
class ScaleResult:
    family: str
    nodes: int
    edges: int
    seed: int
    initial_ms: Optional[int]
    cut_ms: Optional[int]
    heal_ms: Optional[int]
    reboot_ms: Optional[int]
    sampled_loops: int
    data_success: bool
    data_latency_ms: Optional[int]
    tx_frames: int
    wall_seconds: float
    failure: Optional[dict]

    @property
    def correct(self) -> bool:
        return (
            self.initial_ms is not None
            and self.cut_ms is not None
            and self.heal_ms is not None
            and self.reboot_ms is not None
            and self.sampled_loops == 0
            and self.data_success
            and self.failure is None
        )


def validate_cpp(
    family: str,
    n: int,
    *,
    seed: int,
    binary: str,
    budget_ms: int = 2_400_000,
    tick_ms: int = 100,
) -> ScaleResult:
    edges = topology_edges(family, n, seed)
    scenario = Scenario(
        seed=seed,
        nodes=[NodeSpec(node) for node in range(1, n + 1)],
        links=[
            LinkSpec(a, b, latency_ms=0, jitter_ms=0)
            for a, b in edges
        ],
    )
    started_wall = time.time()
    network = scenario.build("cpp", binary=binary, tick_ms=tick_ms)
    failure = None
    loops = 0
    initial_ms = cut_ms = heal_ms = reboot_ms = None
    data_success = False
    data_latency = None
    try:
        initial_ms, observed, last = run_until_correct(network, n, edges, budget_ms)
        loops += observed
        if initial_ms is None:
            failure = {"phase": "initial", **{key: len(value) if isinstance(value, list) else value for key, value in last.items()}}
        else:
            # Prefer a bridge where one exists, otherwise deterministically cut
            # an ordinary edge.  The expected graph is recomputed independently.
            cut = edges[(seed - 1) % len(edges)]
            down_edges = [edge for edge in edges if edge != cut]
            network.set_link(*cut, False)
            cut_ms, observed, last = run_until_correct(network, n, down_edges, budget_ms)
            loops += observed
            if cut_ms is None:
                failure = {"phase": "cut", "edge": cut, **{key: len(value) if isinstance(value, list) else value for key, value in last.items()}}
            else:
                network.set_link(*cut, True)
                heal_ms, observed, last = run_until_correct(network, n, edges, budget_ms)
                loops += observed
                if heal_ms is None:
                    failure = {"phase": "heal", "edge": cut, **{key: len(value) if isinstance(value, list) else value for key, value in last.items()}}
                else:
                    reboot_node = 1 + (seed % n)
                    reboot_started = int(network.now)
                    network.fail_node_at(reboot_started + 100, reboot_node)
                    network.recover_node_at(reboot_started + 1_100, reboot_node)
                    network.run(reboot_started + 1_200)
                    reboot_ms, observed, last = run_until_correct(network, n, edges, budget_ms)
                    loops += observed
                    if reboot_ms is None:
                        failure = {"phase": "reboot", "node": reboot_node, **{key: len(value) if isinstance(value, list) else value for key, value in last.items()}}
                    else:
                        source, target = 1, n
                        if target not in network.routes(source):
                            source = min(network.nodes)
                            target = max(network.routes(source), key=lambda dest: network.routes(source)[dest][1])
                        sent_at = int(network.now)
                        network.send(source, target, b"cpp-scale", timeout_ms=900_000, e2e_ack=True)
                        for elapsed in range(30_000, 1_200_001, 30_000):
                            network.run(sent_at + elapsed)
                            if any(ok == 1 for ok, _ in network.app_acks(source)):
                                data_success = True
                                data_latency = elapsed
                                break
                        if not data_success:
                            failure = {"phase": "data", "source": source, "target": target}
        return ScaleResult(
            family=family,
            nodes=n,
            edges=len(edges),
            seed=seed,
            initial_ms=initial_ms,
            cut_ms=cut_ms,
            heal_ms=heal_ms,
            reboot_ms=reboot_ms,
            sampled_loops=loops,
            data_success=data_success,
            data_latency_ms=data_latency,
            tx_frames=network.rf_metrics.tx_frames,
            wall_seconds=round(time.time() - started_wall, 3),
            failure=failure,
        )
    finally:
        network.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default=os.environ.get("DTP_CPP_NODE"))
    parser.add_argument("--sizes", nargs="+", type=int, default=[32, 64, 96])
    parser.add_argument("--families", nargs="+", default=["line"])
    parser.add_argument("--seed", type=int, default=90_001)
    parser.add_argument("--budget-ms", type=int, default=2_400_000)
    args = parser.parse_args()
    if not args.binary:
        parser.error("--binary or DTP_CPP_NODE is required")
    rows = []
    for family in args.families:
        for index, n in enumerate(args.sizes):
            result = validate_cpp(
                family,
                n,
                seed=args.seed + index,
                binary=args.binary,
                budget_ms=args.budget_ms,
            )
            rows.append({**asdict(result), "correct": result.correct})
    output = {
        "runs": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "rows": rows,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    raise SystemExit(0 if output["correct"] == output["runs"] else 1)


if __name__ == "__main__":
    main()
