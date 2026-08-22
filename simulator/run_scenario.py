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
    "feasible": Profile.feasible,
    "cryst-v2": Profile.crystallized_v2,
}


def normalize_routes(routes: Dict[int, Dict[int, Tuple[int, int]]]):
    return {
        str(node): {
            str(dest): {"next_hop": value[0], "distance": value[1]}
            for dest, value in sorted(table.items())
        }
        for node, table in sorted(routes.items())
    }


def run_one(scenario, backend, duration_ms, profile_name, binary, tick_ms):
    profile = PROFILES[profile_name]() if backend == "python" else None
    network = scenario.build(
        backend,
        profile=profile,
        binary=binary,
        tick_ms=tick_ms,
    )
    try:
        network.run(duration_ms)
        result = {
            "backend": backend,
            "world_time_ms": network.now,
            "routes": normalize_routes(route_snapshot(network)),
            "environment": {
                "node_epoch": dict(sorted(network.node_epoch.items())),
                "link_epoch": {
                    f"{min(link.a, link.b)}-{max(link.a, link.b)}": link.epoch
                    for link in network.links.values()
                },
                "rf_metrics": asdict(network.rf_metrics),
            },
        }
        if hasattr(network, "medium_metrics"):
            result["environment"]["medium_metrics"] = asdict(network.medium_metrics)
        if hasattr(network, "metrics"):
            result["protocol_metrics"] = asdict(network.metrics)
        if hasattr(network, "audit"):
            result["audit"] = network.audit()
        return result
    finally:
        network.close()


def route_diff(a, b):
    diff = []
    for node in sorted(set(a) | set(b), key=int):
        left, right = a.get(node, {}), b.get(node, {})
        for dest in sorted(set(left) | set(right), key=int):
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
        description="Replay one physical scenario against Python theory, real C++, or both"
    )
    parser.add_argument("scenario", type=pathlib.Path)
    parser.add_argument("--backend", choices=("python", "cpp", "both"), default="both")
    parser.add_argument("--duration-ms", type=float, required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="current")
    parser.add_argument("--cpp-binary", default=None)
    parser.add_argument("--tick-ms", type=float, default=50.0)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    scenario = Scenario.from_json(args.scenario.read_text())
    if args.backend in ("cpp", "both") and not args.cpp_binary and not CppNodeProcess.available():
        raise SystemExit(
            "C++ backend is not built. Run: cmake -S simulator/cpp -B simulator/cpp/build && "
            "cmake --build simulator/cpp/build"
        )

    if args.backend == "both":
        py = run_one(scenario, "python", args.duration_ms, args.profile, args.cpp_binary, args.tick_ms)
        cpp = run_one(scenario, "cpp", args.duration_ms, args.profile, args.cpp_binary, args.tick_ms)
        output = {
            "scenario_seed": scenario.seed,
            "python": py,
            "cpp": cpp,
            "route_diff": route_diff(py["routes"], cpp["routes"]),
        }
    else:
        output = run_one(
            scenario,
            args.backend,
            args.duration_ms,
            args.profile,
            args.cpp_binary,
            args.tick_ms,
        )

    print(json.dumps(output, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
