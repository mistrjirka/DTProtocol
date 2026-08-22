#!/usr/bin/env python3
"""Random cut/heal and delivery matrix using the real C++ DTPK backend."""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import deque
from pathlib import Path

from scenario import LinkSpec, NodeSpec, Scenario


def _edges(kind: str, nodes: int, rng: random.Random) -> list[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    if kind == "line":
        edges.update((node, node + 1) for node in range(1, nodes))
    elif kind == "ring":
        edges.update((node, node + 1) for node in range(1, nodes))
        if nodes > 2:
            edges.add((1, nodes))
    elif kind == "star":
        edges.update((1, node) for node in range(2, nodes + 1))
    else:
        for node in range(2, nodes + 1):
            parent = rng.randint(1, node - 1)
            edges.add((min(parent, node), max(parent, node)))
        for left in range(1, nodes + 1):
            for right in range(left + 1, nodes + 1):
                if (left, right) not in edges and rng.random() < 0.15:
                    edges.add((left, right))
    return sorted(edges)


def _truth(net, nodes: int) -> dict[int, dict[int, int]]:
    result = {}
    for source in range(1, nodes + 1):
        distance = {source: 0}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            for neighbor in net.neighbors(current):
                if neighbor not in distance:
                    distance[neighbor] = distance[current] + 1
                    queue.append(neighbor)
        result[source] = distance
    return result


def _audit(net, nodes: int) -> dict:
    truth = _truth(net, nodes)
    routes = {node: net.routes(node) for node in range(1, nodes + 1)}
    missing = stale = wrong = loops = 0
    for source in range(1, nodes + 1):
        for destination, expected in truth[source].items():
            if source == destination:
                continue
            route = routes[source].get(destination)
            if route is None:
                missing += 1
                continue
            if route[1] != expected:
                wrong += 1
            current = source
            seen = set()
            for _ in range(nodes + 1):
                if current == destination:
                    break
                if current in seen:
                    loops += 1
                    break
                seen.add(current)
                selected = routes.get(current, {}).get(destination)
                if selected is None:
                    break
                current = selected[0]
            else:
                loops += 1
        for destination in routes[source]:
            if destination not in truth[source]:
                stale += 1
    return {
        "correct": not (missing or stale or wrong or loops),
        "missing": missing,
        "stale": stale,
        "wrong_distance": wrong,
        "loops": loops,
    }


def _run_until(net, nodes: int, maximum: int, step: int = 50_000) -> tuple[int | None, dict]:
    audit = _audit(net, nodes)
    if audit["correct"]:
        return int(net.now), audit
    when = int(net.now)
    while when < maximum:
        when = min(maximum, when + step)
        net.run(when)
        audit = _audit(net, nodes)
        if audit["correct"]:
            return when, audit
    return None, audit


def _one(seed: int) -> dict:
    rng = random.Random(seed * 10_007)
    nodes = rng.choice((5, 8, 12, 20))
    kind = rng.choice(("line", "ring", "star", "random"))
    loss = rng.choice((0.0, 0.02, 0.05, 0.10))
    edges = _edges(kind, nodes, rng)
    scenario = Scenario(
        seed=130_000 + seed,
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
    net = scenario.build(
        "cpp",
        binary=os.environ.get("DTP_CPP_NODE"),
        tick_ms=100,
    )
    try:
        initial_at, initial = _run_until(
            net,
            nodes,
            maximum=max(600_000, nodes * 45_000),
        )
        cut = rng.choice(edges)
        transient_loops = 0
        net.set_link(cut[0], cut[1], False)
        cut_max = int(net.now) + max(300_000, nodes * 15_000)
        for when in range(int(net.now) + 20_000, cut_max + 1, 20_000):
            net.run(when)
            transient_loops += _audit(net, nodes)["loops"]
        down = _audit(net, nodes)

        net.set_link(cut[0], cut[1], True)
        heal_at, healed = _run_until(
            net,
            nodes,
            maximum=int(net.now) + max(400_000, nodes * 20_000),
            step=20_000,
        )
        app_success = False
        if healed["correct"]:
            source, target = rng.sample(range(1, nodes + 1), 2)
            before = len(net.app_acks(source))
            packet_id = net.send(
                source,
                target,
                b"real-cpp-random",
                timeout_ms=max(120_000, nodes * 8_000),
                e2e_ack=True,
            )
            net.run(net.now + max(240_000, nodes * 10_000))
            app_success = packet_id != 0 and any(
                result == 1 for result, _ping in net.app_acks(source)[before:]
            )
        return {
            "seed": seed,
            "nodes": nodes,
            "edges": len(edges),
            "topology": kind,
            "loss": loss,
            "cut": cut,
            "initial_at_ms": initial_at,
            "initial": initial,
            "down": down,
            "heal_at_ms": heal_at,
            "healed": healed,
            "transient_loops": transient_loops,
            "app_success": app_success,
        }
    finally:
        net.close()


def run(runs: int) -> dict:
    rows = [_one(seed) for seed in range(1, runs + 1)]
    return {
        "runs": runs,
        "topology_failures": sum(
            not row["initial"]["correct"] or not row["healed"]["correct"]
            for row in rows
        ),
        "loops": sum(row["transient_loops"] for row in rows),
        "app_successes": sum(row["app_success"] for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.runs)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result["topology_failures"] == 0 and result["loops"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
