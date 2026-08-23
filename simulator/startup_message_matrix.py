from __future__ import annotations
import argparse, json, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from cpp_backend import CppNetwork
from test_startup_messages import HeldStartupControlNetwork, _run_until, _received_payloads


def direct_case(seed:int, loss:float, multipart:bool):
    held_keys={(1,2,'HELLO'),(1,2,'CRYST'),(1,2,'CRYST_REQ'),(2,1,'CRYST')}
    net=HeldStartupControlNetwork(seed=seed,tick_ms=25,
        hold=lambda s,r,k:(s,r,k) in held_keys)
    try:
        net.add_node(1); net.add_node(2)
        net.add_link(1,2,loss=loss,ack_loss=loss,latency_ms=0,jitter_ms=0)
        if not _run_until(net,lambda:2 in net.routes(1) and 1 not in net.routes(2),40_000):
            return False,'no-asymmetric-window'
        generator = random.Random(seed ^ 0xA55A)
        payload = (bytes(generator.getrandbits(8) for _ in range(1000))
                   if multipart else f'early-{seed}'.encode())
        if net.send(1,2,payload,timeout_ms=240_000,e2e_ack=True)==0:
            return False,'source-rejected'
        if not _run_until(net,lambda:_received_payloads(net,2)==[payload] and any(x[0]==1 for x in net.app_acks(1)),net.now+300_000):
            return False,'forward-failed'
        if net.routes(2).get(1)!=(1,1):
            return False,'reverse-route-not-learned'
        reply=f'reply-{seed}'.encode()
        if net.send(2,1,reply,timeout_ms=120_000,e2e_ack=True)==0:
            return False,'reverse-rejected'
        if not _run_until(net,lambda:_received_payloads(net,1)==[reply] and any(x[0]==1 for x in net.app_acks(2)),net.now+180_000):
            return False,'reverse-failed'
        net.release_all(); net.hold=lambda _s,_r,_k:False
        net.run(net.now+120_000)
        if net.routes(1).get(2)!=(2,1) or net.routes(2).get(1)!=(1,1):
            return False,'post-release-not-converged'
        if any(x[0]!=1 for x in net.app_acks(1)+net.app_acks(2)):
            return False,'negative-app-ack'
        return True,'ok'
    finally:
        net.close()


def partial_line_case(seed:int, loss:float):
    net=CppNetwork(seed=seed,tick_ms=25)
    try:
        for n in (1,2,3,4): net.add_node(n)
        for a,b in ((1,2),(2,3),(3,4)):
            net.add_link(a,b,loss=loss,ack_loss=loss,latency_ms=0,jitter_ms=0)
        # A partial window may be very short under some phases. Poll every 25 ms.
        if not _run_until(net,lambda:3 in net.routes(1) and 4 not in net.routes(1),90_000):
            # This is not a protocol failure if all four arrived atomically; send
            # as soon as the known route to 3 exists and mark the window coalesced.
            if not _run_until(net,lambda:3 in net.routes(1),120_000):
                return False,'route3-never-known'
        payload=f'partial-{seed}'.encode()
        if net.send(1,3,payload,timeout_ms=180_000,e2e_ack=True)==0:
            return False,'send-rejected'
        if not _run_until(net,lambda:_received_payloads(net,3)==[payload] and any(x[0]==1 for x in net.app_acks(1)),net.now+240_000):
            return False,'delivery-failed'
        net.run(net.now+180_000)
        expected={1:{2,3,4},2:{1,3,4},3:{1,2,4},4:{1,2,3}}
        for n,dests in expected.items():
            if not dests.issubset(net.routes(n)):
                return False,f'final-routes-{n}'
        return True,'ok'
    finally:
        net.close()

def run_matrix(runs: int, losses: tuple[float, ...]):
    matrix = []
    for loss in losses:
        for multipart in (False, True):
            stats = {}
            failures = []
            for index in range(runs):
                seed = (
                    920_000 + int(loss * 1000) * 1000
                    + (10_000 if multipart else 0) + index
                )
                ok, reason = direct_case(seed, loss, multipart)
                stats[reason] = stats.get(reason, 0) + 1
                if not ok and len(failures) < 10:
                    failures.append((seed, reason))
            matrix.append({
                "scenario": "direct-multipart" if multipart else "direct-single",
                "loss": loss,
                "runs": runs,
                "stats": stats,
                "failures": failures,
            })
    for loss in losses:
        stats = {}
        failures = []
        for index in range(runs):
            seed = 960_000 + int(loss * 1000) * 1000 + index
            ok, reason = partial_line_case(seed, loss)
            stats[reason] = stats.get(reason, 0) + 1
            if not ok and len(failures) < 10:
                failures.append((seed, reason))
        matrix.append({
            "scenario": "partial-line",
            "loss": loss,
            "runs": runs,
            "stats": stats,
            "failures": failures,
        })
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproducible real-C++ startup/crystallization message matrix"
    )
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument(
        "--losses", default="0,0.05,0.10",
        help="comma-separated independent DATA and link-ACK loss rates",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs <= 0:
        parser.error("--runs must be positive")
    try:
        losses = tuple(float(value) for value in args.losses.split(","))
    except ValueError as exc:
        parser.error(f"invalid --losses: {exc}")
    if not losses or any(value < 0.0 or value >= 1.0 for value in losses):
        parser.error("loss rates must be in [0,1)")

    matrix = run_matrix(args.runs, losses)
    encoded = json.dumps(matrix, indent=2) + "\n"
    print(encoded, end="")
    if args.output:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded)
        temporary.replace(args.output)
    return 1 if any(item["failures"] for item in matrix) else 0


if __name__ == "__main__":
    raise SystemExit(main())
