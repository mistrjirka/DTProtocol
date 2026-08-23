#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess


def duration_ms(path: pathlib.Path, pace: float) -> int:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    )
    return int(round(float(raw.strip()) * pace * 1000.0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pace", type=float, required=True)
    parser.add_argument("--raw", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("scenes", nargs="+")
    args = parser.parse_args()

    if args.pace <= 0:
        raise SystemExit("pace must be positive")

    titles = {
        "Opening": "Can a half-crystallized mesh send?",
        "Architecture": "Architecture recap",
        "Crystallization": "Transactional crystallization",
        "Feasibility": "Loop-safe feasibility",
        "EarlyData": "Using a route before full convergence",
        "Reliability": "Hop and end-to-end reliability",
        "MultipartCompression": "Compression and multipart repair",
        "Scheduler": "Scheduler and progress",
        "Repair": "Cut/heal route repair",
        "Validation": "Measured validation",
        "Closing": "Summary",
    }

    start = 0
    lines = [";FFMETADATA1"]
    for scene in args.scenes:
        path = args.raw / f"{scene}.mp4"
        if not path.is_file():
            raise SystemExit(f"missing rendered scene: {path}")
        end = start + duration_ms(path, args.pace)
        title = titles.get(scene, scene)
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start}",
                f"END={end}",
                f"title={title}",
            ]
        )
        start = end

    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
