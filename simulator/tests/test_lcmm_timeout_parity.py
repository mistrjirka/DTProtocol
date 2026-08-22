import math
import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from model import Packet, Profile
from radio_timing import rssi_cca_duration_ms, tx_startup_ms
from timed_python_backend import TimedSharedPythonNetwork


def _net():
    net = TimedSharedPythonNetwork(
        seed=1301,
        profile=Profile.crystallized_v2(),
        radio_contention=False,
    )
    node = net.add_node(1, start=False)
    node.up = True
    return net, node


def test_hop_timeout_base_matches_production_request_division():
    net, _node = _net()

    cryst_req = Packet(
        "CRYST_REQ", 1, original_sender=1, final_target=2
    )
    relayed_data = Packet(
        "DATA", 2, original_sender=7, final_target=9, payload_size=8
    )
    source_data = Packet(
        "DATA", 3, original_sender=1, final_target=9, payload_size=8
    )
    net._source_request_timeout_ms[(1, 3)] = 10_000

    assert net._production_hop_timeout_base_ms(1, cryst_req) == 1000
    assert net._production_hop_timeout_base_ms(1, relayed_data) == 1666
    assert net._production_hop_timeout_base_ms(1, source_data) == 3333


def test_node_retry_query_uses_remaining_absolute_deadline():
    net, node = _net()
    packet = Packet(
        "DATA", 8, original_sender=1, final_target=2, payload_size=8
    )
    key = net._deadline_key(1, packet)
    net._hop_deadline_ms[key] = 1500.0

    net.now = 400.0
    assert node.link_retry_timeout_ms(packet) == pytest.approx(1100.0)
    net.now = 1600.0
    assert node.link_retry_timeout_ms(packet) == pytest.approx(1.0)


def test_clear_attempt_deadline_includes_data_airtime_but_not_double_counts_cca_setup():
    net, node = _net()
    packet = Packet(
        "DATA",
        9,
        original_sender=1,
        final_target=2,
        payload_size=12,
        wire_dtpk_size=11 + 12,
    )
    net._source_request_timeout_ms[(1, 9)] = 9000

    frame_bytes = net._frame_bytes(packet)
    cca = rssi_cca_duration_ms()
    setup = tx_startup_ms(frame_bytes)
    request_start = 100.0
    rf_start = request_start + cca + setup
    net.now = rf_start

    # No physical link is required for this deadline check. The parent path will
    # schedule a failed-hop retry, which itself queries the newly stored deadline.
    net._transmit_after_cca(
        1,
        2,
        packet,
        True,
        lambda _ok: None,
        1,
    )

    base_deadline = (
        request_start
        + 3000
        + math.ceil(net.airtime_ms(frame_bytes))
    )
    deadline = net._hop_deadline_ms[net._deadline_key(1, packet)]
    assert base_deadline + 25 <= deadline <= base_deadline + 250
    assert node.link_retry_timeout_ms(packet) == pytest.approx(
        max(1.0, deadline - rf_start)
    )
