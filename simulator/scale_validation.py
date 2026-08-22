from __future__ import annotations

"""Reproducible large-network validation for crystallization-v2.

This is intentionally a correctness/convergence harness, not a dense-radio
capacity benchmark. A line stresses route-table size, multi-chunk CRYST state
and propagation diameter while keeping the expected shortest routes exact.

Examples:

  PYTHONPATH=simulator python simulator/scale_validation.py --backend python
  PYTHONPATH=simulator python simulator/scale_validation.py --backend cpp --tick-ms 50

Use a 10 ms C++ tick when studying latency/timers. The default 50 ms here is an
explicit scale-study tradeoff for dozens of subprocesses; the normal Scenario
API defaults to 10 ms.
"""

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, Tuple

from model import Profile
from scenario import Scenario


@dataclass
class CheckpointResult:
    n: int
    checkpoint_ms: int
    expected_routes: int
    present_routes: int
    missing_routes: int
    wrong_routes: int
    correct_fraction: float
    rf_tx_frames: int
    wall_seconds: float


def audit_line(network, n: int) -> Tuple[int, int, int, int]:
    """Return expected, present, missing and wrong route counts for a line."""
    expected = n * (n - 1)
    present = 0
    missing = 0
    wrong = 0

    for source in range(1, n + 1):
        routes: Dict[int, Tuple[int, int]] = network.routes(source)
        for destination in range(1, n + 1):
            if destination == source:
                continue
            got = routes.get(destination)
            if got is None:
                missing += 1
                continue
            present += 1
            expected_next = source + 1 if destination > source else source - 1
            expected_distance = abs(destination - source)
            if got != (expected_next, expected_distance):
                wrong += 1

    return expected, present, missing, wrong


def run_size(
    backend: str,
    n: int,
    checkpoints: Iterable[int],
    *,
    seed: int,
    tick_ms: float,
    radio_contention: bool,
) -> list[CheckpointResult]:
    scenario = Scenario.line(
        n,
        seed=seed,
        latency_ms=0.0,
        jitter_ms=0.0,
        radio_contention=radio_contention,
    )
    profile = Profile.crystallized_v2() if backend == "python" else None
    network = scenario.build(
        backend,
        profile=profile,
        tick_ms=tick_ms,
    )

    started = time.perf_counter()
    rows: list[CheckpointResult] = []
    try:
        for checkpoint in sorted(set(int(x) for x in checkpoints)):
            network.run(checkpoint)
            expected, present, missing, wrong = audit_line(network, n)
            rf_tx = int(getattr(network.rf_metrics, "tx_frames", 0))
            rows.append(
                CheckpointResult(
                    n=n,
                    checkpoint_ms=checkpoint,
                    expected_routes=expected,
                    present_routes=present,
                    missing_routes=missing,
                    wrong_routes=wrong,
                    correct_fraction=(expected - missing - wrong) / expected
                    if expected
                    else 1.0,
                    rf_tx_frames=rf_tx,
                    wall_seconds=time.perf_counter() - started,
                )
            )
    finally:
        close = getattr(network, "close", None)
        if callable(close):
            close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("python", "cpp"), default="python")
    parser.add_argument("--sizes", default="16,32,48,64,96")
    parser.add_argument("--checkpoints-ms", default="600000,1200000,2400000")
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument(
        "--tick-ms",
        type=float,
        default=50.0,
        help="C++ scale-study tick; use 10 ms for timing-sensitive validation",
    )
    parser.add_argument(
        "--radio-contention",
        action="store_true",
        help=(
            "Enable shared-channel collisions/CCA. Python models busy CCA; "
            "timed C++ busy-CCA semantics remain a documented approximation."
        ),
    )
    args = parser.parse_args()

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    checkpoints = [
        int(x) for x in args.checkpoints_ms.split(",") if x.strip()
    ]

    results = []
    for n in sizes:
        for row in run_size(
            args.backend,
            n,
            checkpoints,
            seed=args.seed + n,
            tick_ms=args.tick_ms,
            radio_contention=args.radio_contention,
        ):
            results.append(asdict(row))
            print(json.dumps(asdict(row), sort_keys=True), flush=True)

    summary = {
        "backend": args.backend,
        "tick_ms": args.tick_ms,
        "radio_contention": args.radio_contention,
        "sizes": sizes,
        "checkpoints_ms": checkpoints,
        "results": results,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
