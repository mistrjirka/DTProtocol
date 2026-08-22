import pathlib
import sys

import pytest

SIM_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM_ROOT))

from cpp_backend import CppNodeProcess, Tx
from environment import MAC_OVERHEAD
from radio_timing import (
    rssi_cca_duration_ms,
    rx_packet_read_ms,
    rx_rearm_ms,
    tx_startup_ms,
)
from scenario import Scenario
from timed_cpp_backend import TimedSharedCppNetwork


requires_cpp = pytest.mark.skipif(
    not CppNodeProcess.available(),
    reason="host C++ node not built",
)


def test_cpp_rf_start_waits_for_clear_cca_and_radio_setup_without_host_binary():
    net = TimedSharedCppNetwork(seed=901, radio_contention=True)
    net.register_node(1, up=True)
    net._radio_rx_ready_at[1] = 0.0

    payload = b"x" * 20
    tx = Tx(token=123, target=0, payload=payload)
    requested_at = 100.0
    frame_bytes = MAC_OVERHEAD + len(payload)
    expected_rf_start = (
        requested_at
        + rssi_cca_duration_ms()
        + tx_startup_ms(frame_bytes)
    )
    expected_rf_end = expected_rf_start + net.airtime_ms(frame_bytes)
    expected_rx_ready = expected_rf_end + rx_rearm_ms()

    net.now = requested_at
    net._start_tx(1, tx, requested_at)

    assert net._medium_tx == []
    assert net._radio_rx_ready_at[1] == pytest.approx(expected_rx_ready)

    net.run_events(expected_rf_start - 1e-6)
    assert net._medium_tx == []

    net.run_events(expected_rf_start)
    assert len(net._medium_tx) == 1
    medium = net._medium_tx[0]
    assert medium["sender"] == 1
    assert medium["start"] == pytest.approx(expected_rf_start)
    assert medium["end"] == pytest.approx(expected_rf_end)

    # Stop before PHYDONE: no subprocess is required for this scheduler test.
    net.run_events(expected_rx_ready - 1e-6)


def test_cpp_receiver_is_not_ready_while_radiolib_reads_rx_buffer():
    net = TimedSharedCppNetwork(seed=902, radio_contention=False)
    net.register_node(1, up=True)
    net.register_node(2, up=True)
    net._radio_rx_ready_at[1] = 0.0
    net._radio_rx_ready_at[2] = 0.0
    net.add_link(1, 2, latency_ms=0.0, jitter_ms=0.0)

    payload = b"y" * 17
    frame_bytes = MAC_OVERHEAD + len(payload)
    rf_start = 10.0
    rf_end = rf_start + net.airtime_ms(frame_bytes)
    sender_epoch, receiver_epoch, link_epoch = net.capture_frame_epochs(1, 2)

    net.now = rf_end
    net._rf_complete_timed(
        1,
        sender_epoch,
        2,
        receiver_epoch,
        2,
        link_epoch,
        rf_start,
        rf_end,
        0.0,
        payload,
    )

    expected_read_done = rf_end + rx_packet_read_ms(frame_bytes)
    assert net._radio_rx_ready_at[2] == pytest.approx(expected_read_done)

    # A new frame beginning before the read finishes cannot be accepted by the
    # bounded C++ radio model even though its RF path is otherwise valid.
    net.now = expected_read_done - 1e-6
    assert not net.frame_start_valid(1, 2)
    net.now = expected_read_done
    assert net.frame_start_valid(1, 2)


@requires_cpp
def test_scenario_cpp_uses_timed_backend_and_still_delivers_acknowledged_data():
    scenario = Scenario.line(2, seed=903, latency_ms=0.0, jitter_ms=0.0)
    with scenario.build("cpp", tick_ms=10.0) as net:
        assert isinstance(net, TimedSharedCppNetwork)
        net.run(60_000)
        assert net.routes(1).get(2) == (2, 1)
        assert net.routes(2).get(1) == (1, 1)

        start = net.now
        packet_id = net.send(
            1, 2, b"timed-cpp", timeout_ms=10_000, e2e_ack=True
        )
        assert packet_id != 0
        net.run(start + 15_000)
        assert any(result == 1 for result, _ping in net.app_acks(1))
