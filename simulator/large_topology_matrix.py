#!/usr/bin/env python3
"""Large sparse/dense topology validation for Python and real-C++ backends."""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import deque
from pathlib import Path
from typing import Callable

from model import Profile
from scenario import LinkSpec, NodeSpec, Scenario
from simulator import Simulator


def _line(nodes: int) -> list[tuple[int, int]]:
    return [(node, node + 1) for node in range(1, nodes)]


def _grid(nodes: int) -> list[tuple[int, int]]:
    width = max(2, int(nodes**0.5))
    edges: set[tuple[int, int]] = set()
    for index in range(nodes):
        node = index + 1
        _row, column = divmod(index, width)
        if column + 1 < width and index + 1 < nodes:
            edges.add((node, node + 1))
        below = index + width
        if below < nodes:
            edges.add((node, below + 1))
    for node in range(2, nodes + 1):
        if not any(node in edge for edge in edges):
            edges.add((node - 1, node))
    return sorted(edges)


def _random_sparse(nodes: int, seed: int) -> list[tuple[int, int]]:
    rng = random.Random(seed)
    edges: set[tuple[int, int]] = set()
    for node in range(2, nodes + 1):
        parent = rng.randint(max(1, node - 8), node - 1)
        edges.add((min(parent, node), max(parent, node)))
    for left in range(1, nodes + 1):
        for right in range(left + 1, min(nodes + 1, left + 10)):
            if (left, right) not in edges and rng.random() < 0.12:
                edges.add((left, right))
    return sorted(edges)


def _build_python(nodes: int, edges: list[tuple[int, int]], seed: int):
    sim = Simulator(seed=seed, profile=Profile.crystallized_v2())
    for node in range(1, nodes + 1):
        sim.add_node(node)
    for left, right in edges:
        sim.add_link(left, right, latency_ms=0, jitter_ms=0)
    return sim


def _build_cpp(nodes: int, edges: list[tuple[int, int]], seed: int, tick_ms: int):
    scenario = Scenario(
        seed=seed,
        nodes=[NodeSpec(node) for node in range(1, nodes + 1)],
        links=[
            LinkSpec(left, right, latency_ms=0, jitter_ms=0)
            for left, right in edges
        ],
    )
    return scenario.build(
        "cpp",
        binary=os.environ.get("DTP_CPP_NODE"),
        tick_ms=tick_ms,
    )


def _truth(network, nodes: int) -> dict[int, dict[int, int]]:
    result: dict[int, dict[int, int]] = {}
    for source in range(1, nodes + 1):
        if not network.node_up.get(source, False):
            result[source] = {}
            continue
        distance = {source: 0}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            for neighbor in network.neighbors(current):
                if neighbor not in distance:
                    distance[neighbor] = distance[current] + 1
                    queue.append(neighbor)
        result[source] = distance
    return result


def _audit(network, nodes: int) -> dict:
    truth = _truth(network, nodes)
    routes = {node: network.routes(node) for node in range(1, nodes + 1)}
    missing = stale = wrong = loops = 0
    for source in range(1, nodes + 1):
        for destination, expected in truth[source].items():
            if destination == source:
                continue
            selected = routes[source].get(destination)
            if selected is None:
                missing += 1
                continue
            if selected[1] != expected:
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
                hop = routes.get(current, {}).get(destination)
                if hop is None:
                    break
                current = hop[0]
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


def _run_until(
    network,
    nodes: int,
    *,
    maximum: int,
    step: int,
) -> tuple[int | None, dict]:
    when = int(network.now)
    # Python nodes schedule startup at the current timestamp. Process those
    # events before the first audit so an unstarted network cannot pass
    # vacuously with empty route tables.
    network.run(network.now)
    final = _audit(network, nodes)
    if final["correct"]:
        return when, final
    while when < maximum:
        when = min(maximum, when + step)
        network.run(when)
        final = _audit(network, nodes)
        if final["correct"]:
            return when, final
    return None, final


def _endpoint_delivery(network, backend: str, source: int, target: int, timeout: int) -> bool:
    if backend == "cpp":
        before = len(network.app_acks(source))
        packet_id = network.send(
            source,
            target,
            b"large-topology",
            timeout_ms=timeout,
            e2e_ack=True,
        )
        network.run(network.now + timeout + 120_000)
        return packet_id != 0 and any(
            result == 1
            for result, _ping in network.app_acks(source)[before:]
        )

    before = network.metrics.e2e_success
    packet_id = network.send(
        source,
        target,
        b"large-topology",
        timeout_ms=timeout,
        e2e_ack=True,
    )
    network.run(network.now + timeout + 120_000)
    return packet_id != 0 and network.metrics.e2e_success > before


def _row(name: str, backend: str, nodes: int, edges: int, phase: str, audit: dict, **extra) -> dict:
    return {
        "name": name,
        "backend": backend,
        "nodes": nodes,
        "edges": edges,
        "phase": phase,
        **audit,
        **extra,
    }


def _run_case(
    *,
    name: str,
    backend: str,
    nodes: int,
    edges: list[tuple[int, int]],
    seed: int,
    maximum: int,
    step: int,
    endpoint_timeout: int,
    mutate: Callable | None = None,
) -> list[dict]:
    network = (
        _build_cpp(nodes, edges, seed, tick_ms=100)
        if backend == "cpp"
        else _build_python(nodes, edges, seed)
    )
    rows: list[dict] = []
    try:
        converged_at, audit = _run_until(
            network,
            nodes,
            maximum=maximum,
            step=step,
        )
        delivery = False
        if audit["correct"]:
            delivery = _endpoint_delivery(
                network,
                backend,
                1,
                nodes,
                endpoint_timeout,
            )
        rows.append(
            _row(
                name,
                backend,
                nodes,
                len(edges),
                "stable",
                audit,
                converged_at_ms=converged_at,
                endpoint_delivery=delivery,
            )
        )

        if mutate is not None and audit["correct"]:
            rows.extend(mutate(network, backend, name, nodes, edges))
    finally:
        network.close()
    return rows


def _line_cut_heal(network, backend: str, name: str, nodes: int, edges) -> list[dict]:
    left, right = nodes // 2, nodes // 2 + 1
    network.set_link(left, right, False)
    cut_max = int(network.now) + 600_000
    cut_at, cut_audit = _run_until(network, nodes, maximum=cut_max, step=20_000)
    rows = [
        _row(
            name,
            backend,
            nodes,
            len(edges),
            "cut",
            cut_audit,
            converged_at_ms=cut_at,
            endpoint_delivery="partitioned",
        )
    ]
    network.set_link(left, right, True)
    heal_max = int(network.now) + 600_000
    heal_at, heal_audit = _run_until(network, nodes, maximum=heal_max, step=20_000)
    delivery = False
    if heal_audit["correct"]:
        delivery = _endpoint_delivery(network, backend, 1, nodes, 300_000)
    rows.append(
        _row(
            name,
            backend,
            nodes,
            len(edges),
            "heal",
            heal_audit,
            converged_at_ms=heal_at,
            endpoint_delivery=delivery,
        )
    )
    return rows


def run() -> list[dict]:
    rows: list[dict] = []
    rows += _run_case(
        name="python-line-64",
        backend="python",
        nodes=64,
        edges=_line(64),
        seed=91_064,
        maximum=3_000_000,
        step=100_000,
        endpoint_timeout=300_000,
        mutate=_line_cut_heal,
    )
    rows += _run_case(
        name="python-line-96",
        backend="python",
        nodes=96,
        edges=_line(96),
        seed=91_096,
        maximum=4_000_000,
        step=100_000,
        endpoint_timeout=500_000,
    )
    rows += _run_case(
        name="python-grid-100",
        backend="python",
        nodes=100,
        edges=_grid(100),
        seed=91_100,
        maximum=2_500_000,
        step=100_000,
        endpoint_timeout=240_000,
    )
    rows += _run_case(
        name="python-random-128",
        backend="python",
        nodes=128,
        edges=_random_sparse(128, 91_128),
        seed=91_128,
        maximum=3_000_000,
        step=100_000,
        endpoint_timeout=300_000,
    )
    rows += _run_case(
        name="cpp-line-40",
        backend="cpp",
        nodes=40,
        edges=_line(40),
        seed=92_040,
        maximum=2_000_000,
        step=100_000,
        endpoint_timeout=300_000,
    )
    rows += _run_case(
        name="cpp-line-64",
        backend="cpp",
        nodes=64,
        edges=_line(64),
        seed=92_064,
        maximum=3_000_000,
        step=100_000,
        endpoint_timeout=500_000,
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    valid = all(
        row["correct"]
        and row["endpoint_delivery"] is not False
        for row in result
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
