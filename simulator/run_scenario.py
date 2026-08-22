#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import asdict
from typing import Dict, Tuple

from cpp_backend import CppNodeProcess
from model import Profile
from scenario import Scenario, route_snapshot


PROFILES = {
    "current": Profile.current,
    "intended": Profile.intended,
    "robust": Profile.robust,
}


def normalize_routes(routes: Dict[int, Dict[int, Tuple[int, int]]]):
    return {
        str(node): {
            str(dest): {"next_hop": value[0], "distance": value[1]}
            for dest, value in sorted(table.items())
        }
        for node, table in sorted(routes.items())
    }


def run_one(scenario: Scenario, backend: str, duration_ms: float,
            profile_name: str, binary: str | None, tick_ms: float):
    profile = PROFILES[profile_name]() if backend == "python" else None
    network = scenario.build(
        backend,
        profile=profile,
        binary=binary,
        tick_ms=tick_ms,
    )
    try:
        network.run(duration_ms)
        routes = route_snapshot(network)
        result = {
            "backend": backend,
            "world_time_ms": network.now,
            "routes": normalize_routes(routes),
            "environment": {
                "node_epoch": dict(sorted(network.node_epoch.items())),
                "link_epoch": {
                    f"{min(link.a, link.b)}-{max(link.a, link.b)}": link.epoch
                    for link in network.links.values()
                },
                "rf_metrics": asdict(network.rf_metrics),
            },
        }
        medium = getattr(network, "medium_metrics", None)
        if medium is not None:
            result["environment"]["medium_metrics"] = asdict(medium)
        metrics = getattr(network, "metrics", None)
        if metrics is not None:
            result["protocol_metrics"] = asdict(metrics)
        audit = getattr(network, "audit", None)
        if audit is not None:
            result["audit"] = audit()
        return result
    finally:
        network.close()


def route_diff(a, b):
    diff = []
    nodes = sorted(set(a) | set(b), key=int)
    for node in nodes:
        left = a.get(node, {})
        right = b.get(node, {})
        dests = sorted(set(left) | set(right), key=int)
        for dest in dests:
            if left.get(dest) != right.get(dest):
                diff.append({
                    "node": int(node),
                    "destination": int(dest),
                    "python": left.get(dest),
                    "cpp": right.get(dest),
                })
    return diff


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay one DTProtocol environment trace against Python, real C++, or both"
    )
    parser.add_argument("scenario", type=pathlib.Path)
    parser.add_argument("--backend", choices=("python", "cpp", "both"), default="both")
    parser.add_argument("--duration-ms", type=float, required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="current",
                        help="Python theoretical model profile")
    parser.add_argument("--cpp-binary", default=None)
    parser.add_argument("--tick-ms", type=float, default=50.0,
                        help="host C++ firmware loop tick period")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    scenario = Scenario.from_json(args.scenario.read_text())
    if args.backend in ("cpp", "both") and not args.cpp_binary and not CppNodeProcess.available():
        raise SystemExit(
            "C++ backend is not built. Run: cmake -S simulator/cpp -B simulator/cpp/build && "
            "cmake --build simulator/cpp/build"
        )

    if args.backend == "python":
        output = run_one(
            scenario, "python", args.duration_ms, args.profile,
            args.cpp_binary, args.tick_ms,
        )
    elif args.backend == "cpp":
        output = run_one(
            scenario, "cpp", args.duration_ms, args.profile,
            args.cpp_binary, args.tick_ms,
        )
    else:
        py = run_one(
            scenario, "python", args.duration_ms, args.profile,
            args.cpp_binary, args.tick_ms,
        )
        cpp = run_one(
            scenario, "cpp", args.duration_ms, args.profile,
            args.cpp_binary, args.tick_ms,
        )
        output = {
            "scenario_seed": scenario.seed,
            "python": py,
            "cpp": cpp,
            "route_diff": route_diff(py["routes"], cpp["routes"]),
        }

    print(json.dumps(output, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
