from __future__ import annotations

import argparse
import json
from dataclasses import replace
from statistics import mean

from dtpsim import Profile, line_topology, random_graph


def convergence_by_size(profile: Profile, sizes=range(2, 9), runs=200, duration=180_000):
    rows = []
    for n in sizes:
        ok = 0
        cryst = []
        bytes_air = []
        for seed in range(1, runs + 1):
            sim = line_topology(n, seed=seed, profile=profile)
            sim.run(duration)
            ok += int(sim.audit()["correct"])
            cryst.append(sim.metrics.cryst_tx)
            bytes_air.append(sim.metrics.bytes_on_air)
        rows.append({
            "n": n,
            "correct_rate": ok / runs,
            "mean_cryst_tx": mean(cryst),
            "mean_bytes_on_air": mean(bytes_air),
        })
    return rows


def delivery_by_hops(base_profile: Profile, max_hops=8, runs=500, loss=0.1):
    # Use robust topology maintenance, then selectively restore current data-plane bugs.
    profiles = {
        "current-data": replace(
            Profile.robust(),
            name="current-data",
            global_e2e_gate=True,
            ack_bypasses_e2e_gate=False,
            relay_lcmm_ack=False,
            forwarding_size_bug=True,
            nack_is_success=True,
            ack_null_route_crash=True,
            noack_releases_lcmm_immediately=True,
            mac_busy_silent_drop=True,
            duplicate_suppression=False,
        ),
        "fixed-data": replace(Profile.robust(), name="fixed-data"),
    }
    rows = []
    for name, profile in profiles.items():
        for h in range(1, max_hops + 1):
            success = 0
            failure = 0
            delivered = 0
            frames = []
            for seed in range(1, runs + 1):
                sim = line_topology(h + 1, seed=seed, profile=profile, loss=loss)
                sim.run(180_000)
                if not sim.audit()["correct"]:
                    continue
                sim.nodes[1].send_data(h + 1, payload_size=32, e2e_ack=True, timeout_ms=20_000)
                sim.run(230_000)
                success += sim.metrics.e2e_success
                failure += sim.metrics.e2e_failure
                delivered += sim.metrics.delivered_app
                frames.append(sim.metrics.radio_data_frames + sim.metrics.radio_link_ack_frames)
            denom = success + failure
            rows.append({
                "profile": name,
                "hops": h,
                "success_rate": success / denom if denom else None,
                "app_deliveries_per_trial": delivered / max(1, denom),
                "mean_total_radio_frames": mean(frames) if frames else None,
            })
    return rows


def reconnect_random(profile: Profile, n=6, runs=200, p=0.25, loss=0.03):
    ok = loops = stale = missing = 0
    for seed in range(1, runs + 1):
        sim = random_graph(n, p, seed=seed, profile=profile, loss=loss)
        sim.run(180_000)
        # Choose a backbone link so a real link churn event always occurs.
        a = 1 + (seed % (n - 1))
        b = a + 1
        sim.set_link(a, b, False)
        sim.run(260_000)
        sim.set_link(a, b, True)
        sim.run(400_000)
        audit = sim.audit()
        ok += int(audit["correct"])
        loops += len(audit["loops"])
        stale += len(audit["stale"])
        missing += len(audit["missing"])
    return {
        "profile": profile.name,
        "n": n,
        "runs": runs,
        "correct_rate": ok / runs,
        "loops": loops,
        "stale": stale,
        "missing": missing,
    }


def find_loop(max_nodes=8, seeds=2000):
    # A profile that removes the session-liveness conflation but intentionally keeps
    # naive uint8 distance-vector semantics. This isolates routing-loop safety.
    profile = replace(
        Profile.robust(),
        name="loop-search",
        periodic_cryst_ms=3_000,
        neighbor_expiry_ms=12_000,
        k_limit_ms=1_000,
        cryst_jitter_min_ms=50,
        distance_uint8_wrap=True,
        max_metric=None,
        session_gc_enabled=False,
    )
    for n in range(4, max_nodes + 1):
        for seed in range(1, seeds + 1):
            sim = random_graph(n, 0.35, seed=seed, profile=profile, loss=0.0)
            sim.run(60_000)
            if not sim.audit()["correct"]:
                continue
            # Repeatedly cut one backbone edge and inspect transient states while updates run.
            a = 1 + (seed % (n - 1))
            b = a + 1
            sim.set_link(a, b, False)
            for t in range(61_000, 140_001, 1_000):
                sim.run(t)
                audit = sim.audit()
                if audit["loops"]:
                    return {
                        "n": n,
                        "seed": seed,
                        "cut": [a, b],
                        "time_ms": t,
                        "loops": audit["loops"],
                        "routes": sim.summary()["routes"],
                        "trace_tail": sim.trace[-80:],
                    }
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment", choices=["convergence", "delivery", "reconnect", "find-loop"])
    ap.add_argument("--profile", choices=["current", "intended", "robust"], default="robust")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--loss", type=float, default=0.1)
    args = ap.parse_args()
    profile = {"current": Profile.current(), "intended": Profile.intended(), "robust": Profile.robust()}[args.profile]
    if args.experiment == "convergence":
        out = convergence_by_size(profile, runs=args.runs)
    elif args.experiment == "delivery":
        out = delivery_by_hops(profile, runs=args.runs, loss=args.loss)
    elif args.experiment == "reconnect":
        out = reconnect_random(profile, runs=args.runs, loss=args.loss)
    else:
        out = find_loop(seeds=args.runs)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
