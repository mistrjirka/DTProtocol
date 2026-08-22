#!/usr/bin/env python3
"""Five-second lossless mobile discovery using the real C++ DTPK processes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scenario import LinkSpec, NodeSpec, Scenario


def run(seeds: int, contact_ms: int = 5_000) -> dict:
    successes = 0
    failures: list[dict] = []
    frame_counts: list[int] = []
    for seed in range(1, seeds + 1):
        scenario = Scenario(
            seed=120_000 + seed,
            nodes=[NodeSpec(1, mobile_hint=True), NodeSpec(2)],
            links=[
                LinkSpec(1, 2, latency_ms=0, jitter_ms=0, up=False)
            ],
        )
        net = scenario.build(
            "cpp",
            binary=os.environ.get("DTP_CPP_NODE"),
            tick_ms=50,
        )
        try:
            phase = 15_000 + ((seed * 7_919) % 20_000)
            net.run(phase)
            before = net.rf_metrics.tx_frames
            net.set_link(1, 2, True)
            net.run(phase + contact_ms)
            routes_1 = net.routes(1)
            routes_2 = net.routes(2)
            success = routes_1.get(2) == (2, 1) and routes_2.get(1) == (1, 1)
            successes += success
            frame_counts.append(net.rf_metrics.tx_frames - before)
            if not success and len(failures) < 20:
                failures.append(
                    {
                        "seed": seed,
                        "phase_ms": phase,
                        "routes_1": routes_1,
                        "routes_2": routes_2,
                    }
                )
        finally:
            net.close()
    return {
        "seeds": seeds,
        "contact_ms": contact_ms,
        "successes": successes,
        "probability": successes / seeds,
        "mean_frames": sum(frame_counts) / len(frame_counts),
        "failure_examples": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=500)
    parser.add_argument("--contact-ms", type=int, default=5_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.seeds, args.contact_ms)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result["successes"] == result["seeds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
