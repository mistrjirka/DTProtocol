from __future__ import annotations

import argparse
import json
import random
from typing import Optional

from model import Profile
from simulator import Simulator


def profile_by_name(name: str) -> Profile:
    return {
        "current": Profile.current(),
        "intended": Profile.intended(),
        "robust": Profile.robust(),
    }[name]


def line_topology(n: int, *, seed: int = 1, profile: Optional[Profile] = None,
                  loss: float = 0.0, ack_loss: Optional[float] = None,
                  latency_ms: float = 25.0, jitter_ms: float = 5.0) -> Simulator:
    sim = Simulator(seed=seed, profile=profile or Profile.current())
    for i in range(1, n + 1):
        sim.add_node(i)
    for i in range(1, n):
        sim.add_link(i, i + 1, loss=loss, ack_loss=ack_loss,
                     latency_ms=latency_ms, jitter_ms=jitter_ms)
    return sim


def ring_topology(n: int, **kwargs) -> Simulator:
    sim = line_topology(n, **kwargs)
    if n > 2:
        sim.add_link(1, n, loss=kwargs.get("loss", 0.0),
                     ack_loss=kwargs.get("ack_loss"),
                     latency_ms=kwargs.get("latency_ms", 25.0),
                     jitter_ms=kwargs.get("jitter_ms", 5.0))
    return sim


def random_graph(n: int, p: float, *, seed: int = 1,
                 profile: Optional[Profile] = None, loss: float = 0.0,
                 ack_loss: Optional[float] = None) -> Simulator:
    """Connected random graph: first add a random spanning tree, then extra edges."""
    rng = random.Random(seed ^ 0xD7A5_71C0)
    sim = Simulator(seed=seed, profile=profile or Profile.current())
    for i in range(1, n + 1):
        sim.add_node(i)
    # Random spanning tree guarantees physical connectivity.
    for i in range(2, n + 1):
        parent = rng.randint(1, i - 1)
        sim.add_link(parent, i, loss=loss, ack_loss=ack_loss)
    for a in range(1, n + 1):
        for b in range(a + 1, n + 1):
            if sim.get_link(a, b) is None and rng.random() < p:
                sim.add_link(a, b, loss=loss, ack_loss=ack_loss)
    return sim


def run_static_line(args):
    sim = line_topology(args.nodes, seed=args.seed, profile=profile_by_name(args.profile),
                        loss=args.loss, ack_loss=args.ack_loss)
    sim.run(args.duration)
    return sim


def run_reconnect(args):
    sim = line_topology(args.nodes, seed=args.seed, profile=profile_by_name(args.profile),
                        loss=args.loss, ack_loss=args.ack_loss)
    first = args.duration * 0.35
    second = args.duration * 0.65
    sim.run(first)
    cut = max(1, args.nodes // 2)
    sim.set_link(cut, cut + 1, False)
    sim.run(second)
    sim.set_link(cut, cut + 1, True)
    sim.run(args.duration)
    return sim


def run_simultaneous(args):
    sim = line_topology(2, seed=args.seed, profile=profile_by_name(args.profile),
                        loss=args.loss, ack_loss=args.ack_loss)
    settle = min(args.duration * 0.7, 60_000)
    sim.run(settle)
    sim.nodes[1].send_data(2, payload_size=args.payload, e2e_ack=True,
                           timeout_ms=args.e2e_timeout)
    sim.nodes[2].send_data(1, payload_size=args.payload, e2e_ack=True,
                           timeout_ms=args.e2e_timeout)
    sim.run(args.duration)
    return sim


def run_send(args):
    sim = line_topology(args.nodes, seed=args.seed, profile=profile_by_name(args.profile),
                        loss=args.loss, ack_loss=args.ack_loss)
    settle = min(args.duration * 0.7, 90_000)
    sim.run(settle)
    sim.nodes[1].send_data(args.nodes, payload_size=args.payload, e2e_ack=True,
                           timeout_ms=args.e2e_timeout)
    sim.run(args.duration)
    return sim


def run_monte_carlo(args):
    correct = delivered = success = failure = 0
    loops = stale = missing = 0
    frame_counts = []
    for seed in range(args.seed, args.seed + args.runs):
        child = argparse.Namespace(**vars(args))
        child.seed = seed
        scenario = args.mc_scenario
        if scenario == "static-line":
            sim = run_static_line(child)
        elif scenario == "reconnect":
            sim = run_reconnect(child)
        elif scenario == "simultaneous":
            sim = run_simultaneous(child)
        else:
            sim = run_send(child)
        audit = sim.audit()
        correct += int(audit["correct"])
        delivered += sim.metrics.delivered_app
        success += sim.metrics.e2e_success
        failure += sim.metrics.e2e_failure
        loops += len(audit["loops"])
        stale += len(audit["stale"])
        missing += len(audit["missing"])
        frame_counts.append(sim.metrics.radio_data_frames + sim.metrics.radio_link_ack_frames)
    denom = success + failure
    print(json.dumps({
        "profile": args.profile,
        "scenario": args.mc_scenario,
        "runs": args.runs,
        "correct_rate": correct / args.runs,
        "e2e_success_rate": success / denom if denom else None,
        "app_deliveries": delivered,
        "loops": loops,
        "stale_routes": stale,
        "missing_routes": missing,
        "mean_radio_frames": sum(frame_counts) / len(frame_counts),
    }, indent=2))
    return None


def build_parser():
    ap = argparse.ArgumentParser(description="DTProtocol/DTPK discrete-event simulator")
    ap.add_argument("scenario", choices=["static-line", "reconnect", "simultaneous", "send", "monte-carlo"])
    ap.add_argument("--profile", choices=["current", "intended", "robust"], default="current")
    ap.add_argument("--nodes", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--duration", type=float, default=120_000)
    ap.add_argument("--loss", type=float, default=0.0)
    ap.add_argument("--ack-loss", type=float, default=None)
    ap.add_argument("--payload", type=int, default=32)
    ap.add_argument("--e2e-timeout", type=int, default=10_000)
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--mc-scenario", choices=["static-line", "reconnect", "simultaneous", "send"], default="static-line")
    return ap


def main():
    args = build_parser().parse_args()
    if args.scenario == "monte-carlo":
        run_monte_carlo(args)
        return
    fn = {
        "static-line": run_static_line,
        "reconnect": run_reconnect,
        "simultaneous": run_simultaneous,
        "send": run_send,
    }[args.scenario]
    sim = fn(args)
    result = sim.summary()
    if args.trace:
        result["trace"] = sim.trace
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
