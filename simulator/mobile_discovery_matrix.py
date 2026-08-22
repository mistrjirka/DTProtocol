#!/usr/bin/env python3
"""Monte-Carlo validation of short-contact mobile discovery.

The pair matrix measures phase-independent five-second contact discovery for
zero, one or two locally hinted mobile nodes.  The crowd matrix separately
measures audible and hidden-terminal stars so a pair-only 100% result cannot
hide response collisions around a mobile hub.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from model import Profile
from scenario import LinkSpec, NodeSpec, Scenario


def _phase(seed: int) -> int:
    # Cover the full 10 s static cadence without coupling to protocol RNG.
    return 15_000 + ((seed * 7_919) % 20_000)


def _pair_trial(seed: int, loss: float, mobile_nodes: int, contact_ms: int) -> dict:
    scenario = Scenario(
        seed=30_000 + seed,
        radio_contention=True,
        nodes=[
            NodeSpec(1, mobile_hint=mobile_nodes >= 1),
            NodeSpec(2, mobile_hint=mobile_nodes >= 2),
        ],
        links=[
            LinkSpec(
                1,
                2,
                loss=loss,
                ack_loss=loss,
                latency_ms=0,
                jitter_ms=0,
                up=False,
            )
        ],
    )
    net = scenario.build("python", profile=Profile.crystallized_v2())
    phase = _phase(seed)
    net.run(phase)
    before = {
        "hello": net.metrics.hello_tx,
        "bytes": net.metrics.bytes_on_air,
        "frames": net.rf_metrics.tx_frames,
        "collisions": net.medium_metrics.collision_drops,
    }
    net.set_link(1, 2, True)
    net.run(phase + contact_ms)
    return {
        "mutual": 2 in net.routes(1) and 1 in net.routes(2),
        "one_way": 2 in net.routes(1) or 1 in net.routes(2),
        "hellos": net.metrics.hello_tx - before["hello"],
        "bytes": net.metrics.bytes_on_air - before["bytes"],
        "frames": net.rf_metrics.tx_frames - before["frames"],
        "collisions": net.medium_metrics.collision_drops - before["collisions"],
    }


def _pair_row(seeds: int, loss: float, mobile_nodes: int, contact_ms: int) -> dict:
    trials = [
        _pair_trial(seed, loss, mobile_nodes, contact_ms)
        for seed in range(1, seeds + 1)
    ]
    mutual = sum(item["mutual"] for item in trials)
    one_way = sum(item["one_way"] for item in trials)
    return {
        "loss": loss,
        "mobile_nodes": mobile_nodes,
        "contact_ms": contact_ms,
        "seeds": seeds,
        "mutual_successes": mutual,
        "mutual_probability": mutual / seeds,
        "one_way_successes": one_way,
        "one_way_probability": one_way / seeds,
        "mean_hellos_during_contact": mean(item["hellos"] for item in trials),
        "mean_bytes_during_contact": mean(item["bytes"] for item in trials),
        "mean_frames_during_contact": mean(item["frames"] for item in trials),
        "mean_collision_drops": mean(item["collisions"] for item in trials),
    }


def _crowd_trial(
    seed: int,
    leaves: int,
    loss: float,
    hidden: bool,
    contact_ms: int,
) -> dict:
    nodes = [NodeSpec(1, mobile_hint=True)]
    links: list[LinkSpec] = []
    for node in range(2, leaves + 2):
        nodes.append(NodeSpec(node))
        links.append(
            LinkSpec(
                1,
                node,
                loss=loss,
                ack_loss=loss,
                latency_ms=0,
                jitter_ms=0,
                up=False,
            )
        )
    if not hidden:
        for left in range(2, leaves + 2):
            for right in range(left + 1, leaves + 2):
                links.append(
                    LinkSpec(
                        left,
                        right,
                        loss=loss,
                        ack_loss=loss,
                        latency_ms=0,
                        jitter_ms=0,
                        up=False,
                    )
                )

    net = Scenario(
        seed=40_000 + seed,
        radio_contention=True,
        nodes=nodes,
        links=links,
    ).build("python", profile=Profile.crystallized_v2())
    phase = _phase(seed)
    net.run(phase)
    before = {
        "hello": net.metrics.hello_tx,
        "bytes": net.metrics.bytes_on_air,
        "frames": net.rf_metrics.tx_frames,
        "collisions": net.medium_metrics.collision_drops,
        "half_duplex": net.medium_metrics.half_duplex_drops,
    }
    for node in range(2, leaves + 2):
        net.set_link(1, node, True)
    if not hidden:
        for left in range(2, leaves + 2):
            for right in range(left + 1, leaves + 2):
                net.set_link(left, right, True)
    net.run(phase + contact_ms)

    successes = [
        node in net.routes(1) and 1 in net.routes(node)
        for node in range(2, leaves + 2)
    ]
    return {
        "all_mutual": all(successes),
        "mutual_pairs": sum(successes),
        "hellos": net.metrics.hello_tx - before["hello"],
        "bytes": net.metrics.bytes_on_air - before["bytes"],
        "frames": net.rf_metrics.tx_frames - before["frames"],
        "collisions": net.medium_metrics.collision_drops - before["collisions"],
        "half_duplex": net.medium_metrics.half_duplex_drops - before["half_duplex"],
    }


def _crowd_row(
    seeds: int,
    leaves: int,
    loss: float,
    hidden: bool,
    contact_ms: int,
) -> dict:
    trials = [
        _crowd_trial(seed, leaves, loss, hidden, contact_ms)
        for seed in range(1, seeds + 1)
    ]
    all_mutual = sum(item["all_mutual"] for item in trials)
    pair_successes = sum(item["mutual_pairs"] for item in trials)
    return {
        "hidden_terminals": hidden,
        "leaves": leaves,
        "loss": loss,
        "contact_ms": contact_ms,
        "seeds": seeds,
        "all_mutual_successes": all_mutual,
        "all_mutual_probability": all_mutual / seeds,
        "pair_successes": pair_successes,
        "pair_probability": pair_successes / (seeds * leaves),
        "mean_hellos_during_contact": mean(item["hellos"] for item in trials),
        "mean_bytes_during_contact": mean(item["bytes"] for item in trials),
        "mean_frames_during_contact": mean(item["frames"] for item in trials),
        "mean_collision_drops": mean(item["collisions"] for item in trials),
        "mean_half_duplex_drops": mean(item["half_duplex"] for item in trials),
    }


def run(seeds: int, crowd_seeds: int, contact_ms: int) -> dict:
    rows = [
        _pair_row(seeds, loss, mobile_nodes, contact_ms)
        for loss in (0.0, 0.05, 0.10, 0.20)
        for mobile_nodes in (0, 1, 2)
    ]
    crowd_rows = [
        _crowd_row(crowd_seeds, leaves, loss, hidden, contact_ms)
        for hidden in (False, True)
        for leaves in (2, 4, 8, 16)
        for loss in (0.0, 0.10)
    ]
    return {
        "profile": "cryst-v2",
        "pair_contact_ms": contact_ms,
        "rows": rows,
        "crowd_rows": crowd_rows,
        "interpretation": {
            "lossless_pair_guarantee": all(
                row["mutual_successes"] == row["seeds"]
                for row in rows
                if row["loss"] == 0.0 and row["mobile_nodes"] > 0
            ),
            "random_loss_not_absolute": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5_000)
    parser.add_argument("--crowd-seeds", type=int, default=500)
    parser.add_argument("--contact-ms", type=int, default=5_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.seeds, args.crowd_seeds, args.contact_ms)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0 if result["interpretation"]["lossless_pair_guarantee"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
