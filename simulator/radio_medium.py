from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class MediumMetrics:
    tx_frames: int = 0
    receptions_checked: int = 0
    collisions: int = 0
    half_duplex_drops: int = 0
    interferers_considered: int = 0


@dataclass
class MediumFrame:
    frame_id: int
    sender: int
    start_ms: float
    end_ms: float
    channel: int = 0
    tag: str = ""
    sender_epoch: int = 0
    # Receiver -> (receiver epoch, link epoch) captured when RF started.
    reachable_epochs: Dict[int, Tuple[int, int]] = field(default_factory=dict)

    def overlaps(self, other: "MediumFrame") -> bool:
        # Touching exactly at one endpoint has zero overlap airtime.
        return self.start_ms < other.end_ms and other.start_ms < self.end_ms


@dataclass(frozen=True)
class ReceptionResult:
    ok: bool
    reason: str = "ok"  # ok, collision, half-duplex
    interferers: Tuple[int, ...] = ()


class SharedRadioMedium:
    """Protocol-independent single-channel LoRa medium.

    This deliberately models only PHY facts common to every backend:

    * a radio cannot receive while it is transmitting;
    * overlapping same-channel LoRa frames that both reach a receiver collide;
    * MAC destination addresses do not make a transmission private RF energy;
    * mobility/failure epochs come from the shared EnvironmentKernel.

    Capture effect, different spreading factors/channels and CAD/LBT policy are
    intentionally separate extensions.  In particular this class does NOT make
    a CCA decision for a sender.  The current real-C++ host backend does not run
    production MAC.cpp yet, so pretending that an ideal LBT policy exists here
    would hide MAC bugs rather than simulate them.
    """

    def __init__(self, environment):
        self.environment = environment
        self.metrics = MediumMetrics()
        self._next_id = 1
        self.frames: List[MediumFrame] = []

    def begin_tx(
        self,
        sender: int,
        start_ms: float,
        end_ms: float,
        *,
        channel: int = 0,
        tag: str = "",
    ) -> int:
        env = self.environment
        reachable: Dict[int, Tuple[int, int]] = {}
        for receiver in env.linked_nodes(sender):
            link = env.get_link(sender, receiver)
            if link is None:
                continue
            if env.frame_start_valid(sender, receiver):
                reachable[receiver] = (
                    env.node_epoch.get(receiver, 0),
                    link.epoch,
                )

        frame = MediumFrame(
            frame_id=self._next_id,
            sender=sender,
            start_ms=float(start_ms),
            end_ms=float(end_ms),
            channel=int(channel),
            tag=tag,
            sender_epoch=env.node_epoch.get(sender, 0),
            reachable_epochs=reachable,
        )
        self._next_id += 1
        self.frames.append(frame)
        self.metrics.tx_frames += 1
        return frame.frame_id

    def frame(self, frame_id: int) -> MediumFrame:
        for frame in reversed(self.frames):
            if frame.frame_id == frame_id:
                return frame
        raise KeyError(frame_id)

    def _frame_can_interfere(self, frame: MediumFrame, receiver: int,
                             overlap_start: float, overlap_end: float) -> bool:
        env = self.environment
        captured = frame.reachable_epochs.get(receiver)
        if captured is None:
            return False
        receiver_epoch, link_epoch = captured
        link = env.get_link(frame.sender, receiver)
        if link is None:
            return False
        if (
            not link.up
            or link.epoch != link_epoch
            or not env.node_up.get(frame.sender, False)
            or env.node_epoch.get(frame.sender, 0) != frame.sender_epoch
            or not env.node_up.get(receiver, False)
            or env.node_epoch.get(receiver, 0) != receiver_epoch
        ):
            return False
        return env.stays_in_range(frame.sender, receiver, overlap_start, overlap_end)

    def reception(self, receiver: int, desired_frame_id: int) -> ReceptionResult:
        env = self.environment
        desired = self.frame(desired_frame_id)
        self.metrics.receptions_checked += 1

        # Half-duplex is a physical property, not a protocol-state property.
        # It catches the important case where the receiver transmitted only in
        # the middle of the desired frame and is back in RX by delivery time.
        for other in self.frames:
            if other.frame_id == desired.frame_id or other.sender != receiver:
                continue
            if other.channel != desired.channel or not desired.overlaps(other):
                continue
            self.metrics.half_duplex_drops += 1
            return ReceptionResult(False, "half-duplex", (receiver,))

        interferers: List[int] = []
        for other in self.frames:
            if other.frame_id == desired.frame_id or other.sender == desired.sender:
                continue
            if other.channel != desired.channel or not desired.overlaps(other):
                continue
            self.metrics.interferers_considered += 1
            overlap_start = max(desired.start_ms, other.start_ms)
            overlap_end = min(desired.end_ms, other.end_ms)
            if overlap_end <= overlap_start:
                continue
            if self._frame_can_interfere(other, receiver, overlap_start, overlap_end):
                interferers.append(other.sender)

        if interferers:
            self.metrics.collisions += 1
            return ReceptionResult(False, "collision", tuple(sorted(set(interferers))))
        return ReceptionResult(True)


def get_medium(environment) -> SharedRadioMedium:
    """Lazily attach the one common medium to an EnvironmentKernel instance."""
    medium = getattr(environment, "_shared_radio_medium", None)
    if medium is None:
        medium = SharedRadioMedium(environment)
        environment._shared_radio_medium = medium
        # Expose metrics under the name already consumed by run_scenario.py.
        environment.medium_metrics = medium.metrics
    return medium
