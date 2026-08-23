from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpp_backend import CppNodeProcess
from test_startup_messages import (
    HeldStartupControlNetwork,
    _received_payloads,
    _run_until,
)

pytestmark = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)

DEFAULT_LOCAL_LIMIT = 8


def test_local_application_admission_is_bounded_without_blocking_crystallization():
    # Hold only routed DTPK ACKs. The first application transaction remains
    # open, so the other locally originated messages stay queued while protocol
    # control traffic is still allowed to discover a newly attached node.
    with HeldStartupControlNetwork(
        seed=95_001,
        tick_ms=25,
        hold=lambda sender, receiver, kind: (
            sender == 2 and receiver == 1 and kind == "ACK"
        ),
    ) as net:
        for node_id in (1, 2, 3):
            net.add_node(node_id)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)

        payloads = [f"bounded-{index}".encode() for index in range(DEFAULT_LOCAL_LIMIT)]
        packet_ids = [
            net.send(1, 2, payload, timeout_ms=240_000, e2e_ack=True)
            for payload in payloads
        ]
        assert all(packet_id != 0 for packet_id in packet_ids)

        # The ninth local transaction fails synchronously and visibly, before
        # compression or payload allocation. It does not enter any protocol
        # queue and therefore cannot displace ACK/repair/control work.
        assert net.send(
            1,
            2,
            b"must-be-rejected",
            timeout_ms=240_000,
            e2e_ack=True,
        ) == 0
        assert net.app_acks(1) == [(0, 0)]

        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == [payloads[0]]
            and bool(net.held),
            net.now + 60_000,
        )

        # Saturated local application admission must not prevent ordinary
        # crystallization from propagating a newly reachable destination.
        net.add_link(2, 3, latency_ms=0, jitter_ms=0)
        assert _run_until(
            net,
            lambda: net.routes(1).get(3) == (2, 2),
            net.now + 120_000,
        ), (net.routes(1), net.routes(2), net.routes(3))

        net.release_all()
        net.hold = lambda _sender, _receiver, _kind: False
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == payloads
            and sum(result == 1 for result, _ping in net.app_acks(1))
            == DEFAULT_LOCAL_LIMIT,
            net.now + 360_000,
        ), (net.nodes[1].events, net.nodes[2].events)
        assert sum(result == 0 for result, _ping in net.app_acks(1)) == 1

        # Admission capacity is released after transactions finish.
        assert net.send(
            1,
            2,
            b"accepted-after-drain",
            timeout_ms=60_000,
            e2e_ack=True,
        ) != 0
        assert _run_until(
            net,
            lambda: _received_payloads(net, 2)
            == payloads + [b"accepted-after-drain"]
            and sum(result == 1 for result, _ping in net.app_acks(1))
            == DEFAULT_LOCAL_LIMIT + 1,
            net.now + 90_000,
        )


def test_no_e2e_local_flood_uses_the_same_bounded_admission():
    with HeldStartupControlNetwork(
        seed=95_002,
        tick_ms=25,
        hold=lambda _sender, _receiver, _kind: False,
    ) as net:
        net.add_node(1)
        net.add_node(2)
        net.add_link(1, 2, latency_ms=0, jitter_ms=0)
        net.run(60_000)

        payloads = [f"noack-{index}".encode() for index in range(DEFAULT_LOCAL_LIMIT)]
        assert all(
            net.send(1, 2, payload, timeout_ms=60_000, e2e_ack=False) != 0
            for payload in payloads
        )
        assert net.send(
            1, 2, b"noack-over-limit", timeout_ms=60_000, e2e_ack=False
        ) == 0
        assert net.app_acks(1) == [(0, 0)]

        assert _run_until(
            net,
            lambda: _received_payloads(net, 2) == payloads,
            net.now + 180_000,
        )
        # No routed ACK callback is produced for accepted no-E2E messages.
        assert net.app_acks(1) == [(0, 0)]
