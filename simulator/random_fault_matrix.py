#!/usr/bin/env python3
"""Deterministic random topology/fault validation for DTProtocol v3."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from model import Profile
from scenario import LinkSpec, NodeSpec, Scenario
from simulator import Simulator


def _topology_edges(kind: str, nodes: int, rng: random.Random) -> list[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    if kind == "line":
        edges.update((node, node + 1) for node in range(1, nodes))
    elif kind == "ring":
        edges.update((node, node + 1) for node in range(1, nodes))
        if nodes > 2:
            edges.add((1, nodes))
    elif kind == "star":
        edges.update((1, node) for node in range(2, nodes + 1))
    elif kind == "grid":
        width = max(2, int(math.sqrt(nodes)))
        for index in range(nodes):
            node = index + 1
            row, column = divmod(index, width)
            if column + 1 < width and index + 1 < nodes:
                edges.add((node, node + 1))
            below = index + width
            if below < nodes:
                edges.add((node, below + 1))
        # Ensure any partial final row remains connected.
        for node in range(2, nodes + 1):
            if not any(node in edge for edge in edges):
                edges.add((node - 1, node))
    else:
        # Start with a random spanning tree, then add density-specific chords.
        for node in range(2, nodes + 1):
            parent = rng.randint(1, node - 1)
            edges.add((min(parent, node), max(parent, node)))
        probability = 0.08 if kind == "random_sparse" else 0.22
        for left in range(1, nodes + 1):
            for right in range(left + 1, nodes + 1):
                if (left, right) not in edges and rng.random() < probability:
                    edges.add((left, right))
    return sorted(edges)


def _build(
    *,
    seed: int,
    nodes: int,
    edges: list[tuple[int, int]],
    loss: float,
    contention: bool,
):
    if contention:
        scenario = Scenario(
            seed=seed,
            radio_contention=True,
            nodes=[NodeSpec(node) for node in range(1, nodes + 1)],
            links=[
                LinkSpec(
                    left,
                    right,
                    loss=loss,
                    ack_loss=loss,
                    latency_ms=0,
                    jitter_ms=5,
                )
                for left, right in edges
            ],
        )
        return scenario.build("python", profile=Profile.crystallized_v2())

    sim = Simulator(seed=seed, profile=Profile.crystallized_v2())
    for node in range(1, nodes + 1):
        sim.add_node(node)
    for left, right in edges:
        sim.add_link(
            left,
            right,
            loss=loss,
            ack_loss=loss,
            latency_ms=0,
            jitter_ms=5,
        )
    return sim


def _counts(audit: dict) -> dict:
    return {
        "correct": bool(audit["correct"]),
        "missing": len(audit["missing"]),
        "stale": len(audit["stale"]),
        "wrong_distance": len(audit["wrong_distance"]),
        "loops": len(audit["loops"]),
    }


def _settle(network, deadline: int, extension: int = 600_000) -> dict:
    network.run(deadline)
    audit = network.audit()
    if not audit["correct"]:
        network.run(deadline + extension)
        audit = network.audit()
    return audit


def _run_one(seed: int, contention: bool) -> dict:
    rng = random.Random(seed * 7_919 + (1 if contention else 0))
    if contention:
        nodes = rng.choice((5, 6, 8, 10, 12))
        loss = rng.choice((0.0, 0.02, 0.05))
        topology = rng.choice(("line", "ring", "star", "grid", "random_sparse"))
    else:
        nodes = rng.choice((6, 10, 20, 40))
        loss = rng.choice((0.0, 0.02, 0.05, 0.10, 0.15))
        topology = rng.choice(
            ("line", "ring", "star", "grid", "random_sparse", "random_dense")
        )
    edges = _topology_edges(topology, nodes, rng)
    network = _build(
        seed=70_000 + seed,
        nodes=nodes,
        edges=edges,
        loss=loss,
        contention=contention,
    )

    initial_deadline = max(300_000, nodes * 25_000)
    initial_audit = _settle(network, initial_deadline)
    initial_end = network.now
    transient_loops = 0
    fault_kind = "cut" if seed % 3 else "reboot"

    if fault_kind == "cut":
        cut = rng.choice(edges)
        network.set_link(cut[0], cut[1], False)
        fault_end = initial_end + max(300_000, nodes * 12_000)
        for when in range(int(initial_end) + 20_000, int(fault_end) + 1, 20_000):
            network.run(when)
            transient_loops += len(network.audit()["loops"])
        fault_audit = network.audit()

        network.set_link(cut[0], cut[1], True)
        heal_end = fault_end + max(300_000, nodes * 12_000)
        for when in range(int(fault_end) + 20_000, int(heal_end) + 1, 20_000):
            network.run(when)
            transient_loops += len(network.audit()["loops"])
        healed_audit = network.audit()
        fault_detail: object = cut
    else:
        node = rng.randint(1, nodes)
        down_at = int(initial_end) + 1
        up_at = down_at + 60_000
        network.fail_node_at(down_at, node)
        network.recover_node_at(up_at, node)
        network.run(down_at + 40_000)
        fault_audit = network.audit()
        heal_end = up_at + max(300_000, nodes * 12_000)
        for when in range(up_at + 20_000, heal_end + 1, 20_000):
            network.run(when)
            transient_loops += len(network.audit()["loops"])
        healed_audit = network.audit()
        fault_detail = node

    app_success = False
    if healed_audit["correct"]:
        source, target = rng.sample(range(1, nodes + 1), 2)
        success_before = network.metrics.e2e_success
        packet_id = network.send(
            source,
            target,
            b"fault-matrix",
            timeout_ms=max(60_000, nodes * 5_000),
            e2e_ack=True,
        )
        network.run(network.now + max(120_000, nodes * 6_000))
        app_success = packet_id != 0 and network.metrics.e2e_success > success_before

    return {
        "seed": seed,
        "nodes": nodes,
        "edges": len(edges),
        "topology": topology,
        "loss": loss,
        "contention": contention,
        "fault": fault_kind,
        "fault_detail": fault_detail,
        "initial": _counts(initial_audit),
        "fault_state": _counts(fault_audit),
        "healed": _counts(healed_audit),
        "transient_loops": transient_loops,
        "app_success": app_success,
        "seq_req_tx": network.metrics.seq_req_tx,
        "bytes_on_air": network.metrics.bytes_on_air,
    }


def run(runs: int, contention_runs: int) -> dict:
    if contention_runs > runs:
        raise ValueError("contention_runs cannot exceed runs")
    rows = [
        _run_one(seed, seed <= contention_runs)
        for seed in range(1, runs + 1)
    ]
    topology_failures = sum(
        not row["initial"]["correct"] or not row["healed"]["correct"]
        for row in rows
    )
    loops = sum(row["transient_loops"] for row in rows)
    app_attempts = sum(row["healed"]["correct"] for row in rows)
    app_successes = sum(row["app_success"] for row in rows)
    return {
        "runs": runs,
        "contention_runs": contention_runs,
        "topology_failures": topology_failures,
        "loops": loops,
        "app_attempts": app_attempts,
        "app_successes": app_successes,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=500)
    parser.add_argument("--contention-runs", type=int, default=75)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.runs, args.contention_runs)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    valid = (
        result["topology_failures"] == 0
        and result["loops"] == 0
        and result["app_successes"] == result["app_attempts"]
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
