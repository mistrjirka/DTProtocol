#!/usr/bin/env python3
"""Minimal DTProtocol v3 BLE gateway client.

Install with: python -m pip install bleak
Examples:
  python tools/dtpk_ble_client.py --name DTPK-LoraWatch --listen
  python tools/dtpk_ble_client.py --name DTPK-StickLiteV3 --recipient 3 --text hello
"""
from __future__ import annotations

import argparse
import asyncio
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
MESSAGE_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
NEIGHBOR_COUNT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

OUTBOUND = 0x01
ACK = 0x02
INBOUND = 0x03
NEIGHBORS = 0x04
INBOUND_FRAGMENT = 0x05

HEADER = struct.Struct("<BHH")
OUTBOUND_PREFIX = struct.Struct("<BHHH")
ACK_FRAME = struct.Struct("<BHHHBH")
INBOUND_PREFIX = struct.Struct("<BHHH")
INBOUND_FRAGMENT_PREFIX = struct.Struct("<BHHHHH")
NEIGHBOR_PREFIX = struct.Struct("<BHHB")
NEIGHBOR = struct.Struct("<HH")


@dataclass
class FragmentAssembly:
    total: int
    data: bytearray = field(init=False)
    present: bytearray = field(init=False)

    def __post_init__(self) -> None:
        self.data = bytearray(self.total)
        self.present = bytearray(self.total)

    def add(self, offset: int, chunk: bytes) -> Optional[bytes]:
        end = offset + len(chunk)
        if offset < 0 or end > self.total:
            return None
        self.data[offset:end] = chunk
        self.present[offset:end] = b"\x01" * len(chunk)
        return bytes(self.data) if all(self.present) else None


class GatewayClient:
    def __init__(self) -> None:
        self.fragments: Dict[Tuple[int, int], FragmentAssembly] = {}
        self.pending_acks: Dict[int, asyncio.Future[Tuple[bool, int]]] = {}
        self._next_message_id = 0

    def next_message_id(self) -> int:
        self._next_message_id = (self._next_message_id + 1) & 0xFFFF
        if self._next_message_id == 0:
            self._next_message_id = 1
        return self._next_message_id

    def on_neighbor_count(self, _sender: object, data: bytearray) -> None:
        if len(data) == 2:
            print(f"route count: {struct.unpack('<H', data)[0]}")

    def on_message(self, _sender: object, raw: bytearray) -> None:
        data = bytes(raw)
        if len(data) < HEADER.size:
            print(f"invalid short notification: {data.hex()}")
            return
        kind, message_id, encoded_length = HEADER.unpack_from(data)
        if encoded_length != len(data):
            print(
                f"invalid notification length: encoded={encoded_length} actual={len(data)}"
            )
            return

        if kind == ACK and len(data) == ACK_FRAME.size:
            _, _, _, original_id, success, ping = ACK_FRAME.unpack(data)
            print(f"delivery ACK id={original_id} success={bool(success)} ping={ping} ms")
            future = self.pending_acks.pop(original_id, None)
            if future and not future.done():
                future.set_result((bool(success), ping))
            return

        if kind == INBOUND and len(data) >= INBOUND_PREFIX.size:
            _, _, _, sender = INBOUND_PREFIX.unpack_from(data)
            payload = data[INBOUND_PREFIX.size :]
            print(f"message from {sender}: {payload!r}")
            return

        if kind == INBOUND_FRAGMENT and len(data) >= INBOUND_FRAGMENT_PREFIX.size:
            _, logical_id, _, sender, total, offset = INBOUND_FRAGMENT_PREFIX.unpack_from(
                data
            )
            key = (sender, logical_id)
            assembly = self.fragments.get(key)
            if assembly is None or assembly.total != total:
                assembly = FragmentAssembly(total)
                self.fragments[key] = assembly
            complete = assembly.add(offset, data[INBOUND_FRAGMENT_PREFIX.size :])
            if complete is not None:
                self.fragments.pop(key, None)
                print(f"multipart message from {sender}: {complete!r}")
            return

        if kind == NEIGHBORS and len(data) >= NEIGHBOR_PREFIX.size:
            _, _, _, count = NEIGHBOR_PREFIX.unpack_from(data)
            expected = NEIGHBOR_PREFIX.size + count * NEIGHBOR.size
            if expected != len(data):
                print(f"invalid route-list notification: {data.hex()}")
                return
            routes = [
                NEIGHBOR.unpack_from(data, NEIGHBOR_PREFIX.size + index * NEIGHBOR.size)
                for index in range(count)
            ]
            print("routes:", ", ".join(f"{node}:{hops}h" for node, hops in routes))
            return

        print(f"unknown notification type=0x{kind:02x}: {data.hex()}")


async def find_device(name: str, timeout: float):
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, advertisement in devices.values():
        advertised_name = advertisement.local_name or device.name or ""
        service_uuids = {value.lower() for value in advertisement.service_uuids or []}
        if advertised_name == name or SERVICE_UUID in service_uuids:
            return device
    raise RuntimeError(f"BLE device {name!r} / DTProtocol service not found")


async def run(args: argparse.Namespace) -> None:
    from bleak import BleakClient

    device = await find_device(args.name, args.scan_timeout)
    gateway = GatewayClient()
    async with BleakClient(device) as client:
        await client.start_notify(MESSAGE_UUID, gateway.on_message)
        await client.start_notify(NEIGHBOR_COUNT_UUID, gateway.on_neighbor_count)
        print(f"connected to {device.name or device.address}")

        if args.recipient is not None:
            payload = (
                open(args.file, "rb").read()
                if args.file
                else args.text.encode("utf-8")
            )
            message_id = gateway.next_message_id()
            frame = OUTBOUND_PREFIX.pack(
                OUTBOUND,
                message_id,
                OUTBOUND_PREFIX.size + len(payload),
                args.recipient,
            ) + payload
            future: asyncio.Future[Tuple[bool, int]] = (
                asyncio.get_running_loop().create_future()
            )
            gateway.pending_acks[message_id] = future
            # `response=True` permits a prepared/long write on backends that
            # expose it; short writes use the same path.
            await client.write_gatt_char(MESSAGE_UUID, frame, response=True)
            try:
                success, ping = await asyncio.wait_for(future, args.ack_timeout)
                if not success:
                    raise RuntimeError("DTProtocol end-to-end delivery failed")
                print(f"delivered in {ping} ms")
            except asyncio.TimeoutError:
                gateway.pending_acks.pop(message_id, None)
                raise RuntimeError("no end-to-end ACK before timeout")

        if args.listen:
            while True:
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(1)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--name", default="DTPK-LoraWatch")
    result.add_argument("--recipient", type=int)
    payload = result.add_mutually_exclusive_group()
    payload.add_argument("--text", default="hello from BLE")
    payload.add_argument("--file")
    result.add_argument("--listen", action="store_true")
    result.add_argument("--scan-timeout", type=float, default=8.0)
    result.add_argument("--ack-timeout", type=float, default=90.0)
    return result


if __name__ == "__main__":
    try:
        asyncio.run(run(parser().parse_args()))
    except KeyboardInterrupt:
        pass
    except ImportError as error:
        raise SystemExit("install the BLE client dependency: python -m pip install bleak") from error
    except Exception as error:
        raise SystemExit(str(error)) from error
