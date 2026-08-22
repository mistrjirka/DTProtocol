#!/usr/bin/env python3
"""Exhaustive lossless topology/cut/heal validation for small graphs."""

from __future__ import annotations

import argparse
import json
from collections import deque
from itertools import combinations
from pathlib import Path

from model import Profile
from simulator import Simulator


def _connected(nodes: int, edges: tuple[tuple[int, int], ...]) -> bool:
    adjacency = [set() for _ in range(nodes)]
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    seen = {0}
    queue = deque([0])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return len(seen) == nodes


def _build(nodes: int, edges: tuple[tuple[int, int], ...], seed: int) -> Simulator:
    sim = Simulator(seed=seed, profile=Profile.crystallized_v2())
    for node in range(nodes):
        sim.add_node(node + 1)
    for left, right in edges:
        sim.add_link(left + 1, right + 1, latency_ms=0, jitter_ms=0)
    return sim


def _audit_counts(audit: dict) -> dict:
    return {
        "missing": len(audit["missing"]),
        "stale": len(audit["stale"]),
        "wrong_distance": len(audit["wrong_distance"]),
        "loops": len(audit["loops"]),
    }


def run(nodes: int) -> dict:
    if nodes > 5:
        raise ValueError(
            "full graph enumeration grows as 2^(n choose 2); use the random "
            "matrix above five nodes"
        )
    possible = tuple(combinations(range(nodes), 2))
    connected_graphs = 0
    cases = 0
    loops_observed = 0
    failures: list[dict] = []

    for mask in range(1, 1 << len(possible)):
        edges = tuple(
            possible[index]
            for index in range(len(possible))
            if (mask >> index) & 1
        )
        if not _connected(nodes, edges):
            continue
        connected_graphs += 1
        seed = 50_000 + mask

        initial = _build(nodes, edges, seed)
        initial.run(250_000)
        initial_audit = initial.audit()
        cases += 1
        if not initial_audit["correct"]:
            failures.append(
                {
                    "phase": "initial",
                    "mask": mask,
                    "edges": edges,
                    "audit": _audit_counts(initial_audit),
                }
            )
            continue

        for edge_index, (left, right) in enumerate(edges):
            sim = _build(nodes, edges, seed + edge_index + 1)
            sim.run(250_000)
            if not sim.audit()["correct"]:
                failures.append(
                    {
                        "phase": "pre_cut",
                        "mask": mask,
                        "edge": (left, right),
                        "audit": _audit_counts(sim.audit()),
                    }
                )
                cases += 1
                continue

            sim.set_link(left + 1, right + 1, False)
            transient_loops = 0
            for when in range(260_000, 600_001, 10_000):
                sim.run(when)
                transient_loops += len(sim.audit()["loops"])
            down_audit = sim.audit()
            loops_observed += transient_loops
            cases += 1
            if transient_loops or not down_audit["correct"]:
                failures.append(
                    {
                        "phase": "cut",
                        "mask": mask,
                        "edge": (left, right),
                        "transient_loops": transient_loops,
                        "audit": _audit_counts(down_audit),
                    }
                )
                continue

            sim.set_link(left + 1, right + 1, True)
            heal_loops = 0
            for when in range(610_000, 950_001, 10_000):
                sim.run(when)
                heal_loops += len(sim.audit()["loops"])
            healed_audit = sim.audit()
            loops_observed += heal_loops
            cases += 1
            if heal_loops or not healed_audit["correct"]:
                failures.append(
                    {
                        "phase": "heal",
                        "mask": mask,
                        "edge": (left, right),
                        "transient_loops": heal_loops,
                        "audit": _audit_counts(healed_audit),
                    }
                )

    return {
        "nodes": nodes,
        "possible_edges": len(possible),
        "connected_graphs": connected_graphs,
        "initial_cut_heal_cases": cases,
        "loops": loops_observed,
        "failures": len(failures),
        "failure_examples": failures[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.nodes)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result["failures"] == 0 and result["loops"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
