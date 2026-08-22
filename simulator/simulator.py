from __future__ import annotations

import random
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from environment import EnvironmentKernel
from model import *
from node import Node


class Simulator(EnvironmentKernel):
    """Python protocol adapter running on the shared RF/environment kernel."""

    def __init__(
        self,
        seed: int = 1,
        profile: Profile | None = None,
        sf: int = 9,
        bandwidth_hz: int = 125_000,
        coding_rate_denominator: int = 7,
    ):
        super().__init__(
            seed,
            sf=sf,
            bandwidth_hz=bandwidth_hz,
            coding_rate_denominator=coding_rate_denominator,
        )
        # Protocol timing/jitter randomness is independent of the environment.
        self.rng = random.Random(seed ^ 0xD7A5_71C0)
        self.profile = profile or Profile.current()
        self.nodes: Dict[int, Node] = {}
        self.metrics = Metrics()

    def add_node(
        self,
        node_id: int,
        start: bool = True,
        *,
        position: Tuple[float, float] = (0.0, 0.0),
    ) -> "Node":
        node = Node(self, node_id)
        self.nodes[node_id] = node
        # Physical environment exists immediately; protocol startup is still an
        # event so all nodes begin deterministically at t=0.
        self.register_node(node_id, up=True, position=position)
        if start:
            self.schedule(0, node.start, priority=self.PROTOCOL_PRIORITY)
        return node

    def _on_environment_node_down(self, node_id: int) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            return
        node.up = False
        node.generation += 1
        node.reset_runtime()

    def _on_environment_node_up(self, node_id: int) -> None:
        node = self.nodes.get(node_id)
        if node is not None:
            node.start()

    def reboot_node(self, node_id: int, downtime_ms: float = 500.0) -> None:
        self.set_node_up(node_id, False, reason="reboot")
        self.schedule(
            downtime_ms,
            self.set_node_up,
            node_id,
            True,
            priority=self.ENV_PRIORITY,
        )

    def neighbors(self, node_id: int, only_up: bool = True) -> List[int]:
        out = super().neighbors(node_id, only_up=only_up)
        if not only_up:
            return out
        return [
            other
            for other in out
            if other in self.nodes
            and self.nodes[other].up
            and not self.nodes[other].crashed
            and node_id in self.nodes
            and self.nodes[node_id].up
            and not self.nodes[node_id].crashed
        ]

    def _frame_bytes(self, packet: Packet) -> int:
        if packet.kind == "CRYST" and packet.wire_dtpk_size:
            dtpk = packet.wire_dtpk_size
        elif packet.kind == "CRYST":
            dtpk = DTPK_CRYST_HEADER + len(packet.advertisements) * NEIGHBOR_RECORD_SIZE
        elif packet.wire_dtpk_size:
            dtpk = packet.wire_dtpk_size
        else:
            dtpk = DTPK_GENERIC_HEADER + packet.payload_size
        return MAC_OVERHEAD + LCMM_OVERHEAD + dtpk

    # ------------------------------------------------------------------
    # Python-model radio/LCMM bridge.
    #
    # C++ LCMM creates actual ACK frames itself.  The abstract Python backend
    # keeps LCMM semantic, but both DATA and ACK airtime traverse the exact same
    # EnvironmentKernel validation (motion, epochs, link state, loss, timing).
    # ------------------------------------------------------------------
    def transmit(
        self,
        sender_id: int,
        target: Optional[int],
        packet: Packet,
        reliable: bool,
        on_complete: Callable[[bool], None],
        attempt: int = 1,
    ) -> None:
        sender = self.nodes[sender_id]
        if (
            not sender.up
            or sender.crashed
            or not self.node_up.get(sender_id, False)
        ):
            self.schedule(0, on_complete, False)
            return

        frame_bytes = self._frame_bytes(packet)
        if frame_bytes > MAX_PACKET_SIZE:
            self.metrics.oversize_drops += 1
            self.log("oversize_drop", node=sender_id, kind=packet.kind, bytes=frame_bytes)
            self.schedule(0, on_complete, False)
            return

        if self.now < sender.radio_busy_until:
            if self.profile.mac_busy_silent_drop:
                self.metrics.silent_busy_drops += 1
                self.log("busy_silent_drop", node=sender_id, kind=packet.kind, target=target)
                if reliable:
                    self.schedule(
                        sender.link_retry_timeout_ms(packet),
                        self._retry_or_finish,
                        sender_id,
                        target,
                        packet,
                        reliable,
                        on_complete,
                        attempt,
                    )
                else:
                    self.schedule(0, on_complete, True)
                return
            self.schedule(
                sender.radio_busy_until - self.now,
                self.transmit,
                sender_id,
                target,
                packet,
                reliable,
                on_complete,
                attempt,
            )
            return

        airtime = self.airtime_ms(frame_bytes)
        rf_start = self.now
        rf_end = rf_start + airtime
        sender.radio_busy_until = rf_end
        self.metrics.radio_data_frames += 1
        self.metrics.bytes_on_air += frame_bytes
        self.rf_metrics.tx_frames += 1

        if target is None:
            self.metrics.broadcasts += 1
            if packet.kind == "CRYST":
                self.metrics.cryst_tx += 1
            self.log("tx_broadcast", node=sender_id, kind=packet.kind, bytes=frame_bytes)

            for receiver in self.linked_nodes(sender_id):
                self.rf_metrics.rf_receivers_considered += 1
                if not self.frame_start_valid(sender_id, receiver):
                    continue
                if self.sample_link_loss(sender_id, receiver):
                    self.metrics.link_loss_drops += 1
                    self.rf_metrics.rf_loss_drops += 1
                    continue
                link = self.get_link(sender_id, receiver)
                assert link is not None
                epochs = self.capture_frame_epochs(sender_id, receiver)
                self.schedule_at(
                    rf_end,
                    self._python_rf_complete,
                    sender_id,
                    receiver,
                    packet.clone(),
                    False,
                    None,
                    rf_start,
                    rf_end,
                    *epochs,
                    self.jittered_latency(link),
                    priority=self.RADIO_PRIORITY,
                )

            self.schedule(
                0 if self.profile.noack_releases_lcmm_immediately else airtime,
                on_complete,
                True,
                priority=self.RADIO_PRIORITY,
            )
            return

        self.metrics.unicast_attempts += 1
        if not self.frame_start_valid(sender_id, target):
            self.log("tx_no_link", node=sender_id, target=target, kind=packet.kind, attempt=attempt)
            if reliable:
                self.schedule(
                    sender.link_retry_timeout_ms(packet),
                    self._retry_or_finish,
                    sender_id,
                    target,
                    packet,
                    reliable,
                    on_complete,
                    attempt,
                )
            else:
                self.schedule(
                    0 if self.profile.noack_releases_lcmm_immediately else airtime,
                    on_complete,
                    True,
                )
            return

        if self.sample_link_loss(sender_id, target):
            self.metrics.link_loss_drops += 1
            self.rf_metrics.rf_loss_drops += 1
            self.log(
                "tx_unicast",
                node=sender_id,
                target=target,
                kind=packet.kind,
                reliable=reliable,
                attempt=attempt,
                lost=True,
            )
            if reliable:
                self.schedule(
                    sender.link_retry_timeout_ms(packet),
                    self._retry_or_finish,
                    sender_id,
                    target,
                    packet,
                    reliable,
                    on_complete,
                    attempt,
                )
            else:
                self.schedule(
                    0 if self.profile.noack_releases_lcmm_immediately else airtime,
                    on_complete,
                    True,
                )
            return

        self.log(
            "tx_unicast",
            node=sender_id,
            target=target,
            kind=packet.kind,
            reliable=reliable,
            attempt=attempt,
            lost=False,
        )
        link = self.get_link(sender_id, target)
        assert link is not None
        epochs = self.capture_frame_epochs(sender_id, target)
        ack_context = (
            sender_id,
            target,
            packet,
            on_complete,
            attempt,
        ) if reliable else None
        self.schedule_at(
            rf_end,
            self._python_rf_complete,
            sender_id,
            target,
            packet.clone(),
            reliable,
            ack_context,
            rf_start,
            rf_end,
            *epochs,
            self.jittered_latency(link),
            priority=self.RADIO_PRIORITY,
        )
        if not reliable:
            self.schedule(
                0 if self.profile.noack_releases_lcmm_immediately else airtime,
                on_complete,
                True,
                priority=self.RADIO_PRIORITY,
            )

    def _python_rf_complete(
        self,
        sender_id: int,
        receiver_id: int,
        packet: Packet,
        reliable: bool,
        ack_context,
        rf_start: float,
        rf_end: float,
        sender_epoch: int,
        receiver_epoch: int,
        link_epoch: int,
        latency_ms: float,
    ) -> None:
        valid, reason = self.frame_path_valid(
            sender_id,
            receiver_id,
            rf_start,
            rf_end,
            sender_epoch,
            receiver_epoch,
            link_epoch,
        )
        if not valid:
            if reason == "range":
                self.rf_metrics.rf_range_drops += 1
            else:
                self.rf_metrics.rf_epoch_drops += 1
            if reliable and ack_context is not None:
                sender, target, original, on_complete, attempt = ack_context
                self.schedule(
                    self.nodes[sender].link_retry_timeout_ms(original),
                    self._retry_or_finish,
                    sender,
                    target,
                    original,
                    True,
                    on_complete,
                    attempt,
                )
            return

        self.schedule(
            latency_ms,
            self._deliver,
            receiver_id,
            sender_id,
            packet,
            reliable,
            ack_context,
            receiver_epoch,
            priority=self.RADIO_PRIORITY,
        )

    def _deliver(
        self,
        receiver_id: int,
        previous_hop: int,
        packet: Packet,
        reliable: bool,
        ack_context,
        receiver_epoch: Optional[int] = None,
    ) -> None:
        receiver = self.nodes[receiver_id]
        if (
            not receiver.up
            or receiver.crashed
            or not self.node_up.get(receiver_id, False)
            or (
                receiver_epoch is not None
                and self.node_epoch.get(receiver_id, 0) != receiver_epoch
            )
        ):
            self.rf_metrics.firmware_epoch_drops += 1
            return

        self.rf_metrics.rf_delivered += 1
        if not reliable:
            receiver.receive(packet, previous_hop)
            return

        sender_id, target, original_packet, on_complete, attempt = ack_context
        link = self.get_link(receiver_id, sender_id)
        if link is None:
            self.schedule(
                receiver.link_retry_timeout_ms(packet),
                self._retry_or_finish,
                sender_id,
                target,
                original_packet,
                True,
                on_complete,
                attempt,
            )
            return

        # LCMM receiver transmits its link ACK first; the DTPK payload is handed
        # upward at TX completion even if that ACK is lost on the reverse path.
        ack_bytes = MAC_OVERHEAD + 1 + 2
        ack_airtime = self.airtime_ms(ack_bytes)
        ack_start = self.now
        ack_end = ack_start + ack_airtime
        self.metrics.radio_link_ack_frames += 1
        self.metrics.bytes_on_air += ack_bytes
        self.rf_metrics.tx_frames += 1

        ack_epochs = self.capture_frame_epochs(receiver_id, sender_id)
        lost = self.sample_link_loss(receiver_id, sender_id, ack=True)
        if lost:
            self.metrics.link_loss_drops += 1
            self.rf_metrics.rf_loss_drops += 1

        self.schedule_at(
            ack_end,
            self._python_ack_rf_complete,
            receiver_id,
            sender_id,
            packet,
            previous_hop,
            original_packet,
            target,
            on_complete,
            attempt,
            ack_start,
            ack_end,
            *ack_epochs,
            lost,
            self.jittered_latency(link),
            priority=self.RADIO_PRIORITY,
        )

    def _python_ack_rf_complete(
        self,
        receiver_id: int,
        sender_id: int,
        packet: Packet,
        previous_hop: int,
        original_packet: Packet,
        target: int,
        on_complete: Callable[[bool], None],
        attempt: int,
        ack_start: float,
        ack_end: float,
        ack_sender_epoch: int,
        ack_receiver_epoch: int,
        link_epoch: int,
        random_lost: bool,
        latency_ms: float,
    ) -> None:
        # The upper layer at the receiver only executes if that device survived
        # its ACK transmission to TX-complete.
        receiver_alive = (
            self.node_up.get(receiver_id, False)
            and self.node_epoch.get(receiver_id, 0) == ack_sender_epoch
            and self.nodes[receiver_id].up
            and not self.nodes[receiver_id].crashed
        )
        if receiver_alive:
            self.nodes[receiver_id].receive(packet, previous_hop)

        valid, reason = self.frame_path_valid(
            receiver_id,
            sender_id,
            ack_start,
            ack_end,
            ack_sender_epoch,
            ack_receiver_epoch,
            link_epoch,
        )
        if not valid:
            if reason == "range":
                self.rf_metrics.rf_range_drops += 1
            else:
                self.rf_metrics.rf_epoch_drops += 1

        if random_lost or not valid:
            self.schedule(
                self.nodes[sender_id].link_retry_timeout_ms(original_packet),
                self._retry_or_finish,
                sender_id,
                target,
                original_packet,
                True,
                on_complete,
                attempt,
            )
            return

        self.schedule(
            latency_ms,
            on_complete,
            True,
            priority=self.RADIO_PRIORITY,
        )

    def _retry_or_finish(
        self,
        sender_id: int,
        target: Optional[int],
        packet: Packet,
        reliable: bool,
        on_complete: Callable[[bool], None],
        attempt: int,
    ) -> None:
        if getattr(on_complete, "_dtp_completed", False):
            return
        if attempt >= self.profile.max_lcmm_attempts:
            on_complete(False)
        else:
            self.transmit(
                sender_id,
                target,
                packet,
                reliable,
                on_complete,
                attempt + 1,
            )

    # ------------------------------------------------------------------
    # Common network-adapter surface
    # ------------------------------------------------------------------
    def routes(self, node_id: int) -> Dict[int, Tuple[int, int]]:
        node = self.nodes.get(node_id)
        if node is None or not node.up or node.crashed:
            return {}
        return {
            dest: (route.next_hop, route.distance)
            for dest, route in node.routes.items()
        }

    def send(
        self,
        node_id: int,
        target: int,
        payload: bytes = b"hello",
        timeout_ms: int = 10000,
        e2e_ack: bool = True,
    ) -> int:
        node = self.nodes.get(node_id)
        if node is None:
            return 0
        packet_id = node.send_data(
            target,
            payload_size=len(payload),
            e2e_ack=e2e_ack,
            timeout_ms=timeout_ms,
        )
        return 0 if packet_id is None else packet_id

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ------------------------------------------------------------------
    # Correctness/audit helpers
    # ------------------------------------------------------------------
    def shortest_distances(self) -> Dict[int, Dict[int, int]]:
        result: Dict[int, Dict[int, int]] = {}
        for source in self.nodes:
            node = self.nodes[source]
            if (
                not node.up
                or node.crashed
                or not self.node_up.get(source, False)
            ):
                result[source] = {}
                continue
            dist = {source: 0}
            q = deque([source])
            while q:
                u = q.popleft()
                for v in self.neighbors(u):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        q.append(v)
            result[source] = dist
        return result

    def audit(self) -> dict:
        truth = self.shortest_distances()
        # Environment-up nodes must also have a running, non-crashed protocol
        # instance. Treating them as absent makes a fresh/unstarted or crashed
        # network vacuously pass every routing property.
        inactive = [
            (
                node_id,
                "crashed" if node.crashed else "not_started",
            )
            for node_id, node in self.nodes.items()
            if self.node_up.get(node_id, False)
            and (not node.up or node.crashed)
        ]
        missing, stale, wrong_distance, loops = [], [], [], []
        stretch_values = []
        for node_id, node in self.nodes.items():
            if not node.up or node.crashed or not self.node_up.get(node_id, False):
                continue
            for dest, d in truth[node_id].items():
                if dest == node_id:
                    continue
                route = node.routes.get(dest)
                if route is None:
                    missing.append((node_id, dest, d))
                else:
                    if route.distance != d:
                        wrong_distance.append((node_id, dest, route.distance, d))
                    path, status = self.follow_route(node_id, dest)
                    if status == "ok":
                        stretch_values.append((len(path) - 1) / d)
                    elif status == "loop":
                        loops.append((node_id, dest, path))
            for dest in list(node.routes):
                if dest not in truth[node_id]:
                    stale.append((node_id, dest))
        self.metrics.loops_observed += len(loops)
        self.metrics.stale_route_observations += len(stale)
        return {
            "inactive": inactive,
            "missing": missing,
            "stale": stale,
            "wrong_distance": wrong_distance,
            "loops": loops,
            "mean_stretch": (
                sum(stretch_values) / len(stretch_values)
                if stretch_values
                else None
            ),
            "correct": not (
                inactive or missing or stale or wrong_distance or loops
            ),
        }

    def follow_route(
        self,
        start: int,
        dest: int,
        max_steps: Optional[int] = None,
    ) -> Tuple[List[int], str]:
        max_steps = max_steps or (len(self.nodes) + 2)
        path = [start]
        seen = {start}
        cur = start
        for _ in range(max_steps):
            if cur == dest:
                return path, "ok"
            node = self.nodes.get(cur)
            if (
                node is None
                or not node.up
                or node.crashed
                or not self.node_up.get(cur, False)
            ):
                return path, "dead"
            route = node.routes.get(dest)
            if route is None:
                return path, "missing"
            nxt = route.next_hop
            link = self.get_link(cur, nxt)
            if (
                link is None
                or not link.up
                or not self.in_range_now(cur, nxt)
            ):
                path.append(nxt)
                return path, "broken"
            if nxt in seen:
                path.append(nxt)
                return path, "loop"
            seen.add(nxt)
            path.append(nxt)
            cur = nxt
        return path, "limit"

    def summary(self) -> dict:
        return {
            "seed": self.seed,
            "profile": self.profile.name,
            "time_ms": self.now,
            "audit": self.audit(),
            "metrics": self.metrics.__dict__.copy(),
            "rf_metrics": self.rf_metrics.__dict__.copy(),
            "routes": {
                nid: {
                    d: {"next": r.next_hop, "distance": r.distance}
                    for d, r in sorted(n.routes.items())
                }
                for nid, n in sorted(self.nodes.items())
            },
            "crashed": [nid for nid, n in self.nodes.items() if n.crashed],
        }
