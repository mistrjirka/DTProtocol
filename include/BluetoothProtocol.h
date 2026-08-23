#ifndef DTPK_BLUETOOTH_PROTOCOL_H
#define DTPK_BLUETOOTH_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

static constexpr uint8_t BLE_MSG_TYPE_OUTBOUND = 0x01;
static constexpr uint8_t BLE_MSG_TYPE_ACK = 0x02;
static constexpr uint8_t BLE_MSG_TYPE_INBOUND = 0x03;
static constexpr uint8_t BLE_MSG_TYPE_NEIGHBORS = 0x04;
static constexpr uint8_t BLE_MSG_TYPE_INBOUND_FRAGMENT = 0x05;

static constexpr char WATCH_SERVICE_UUID[] =
    "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
static constexpr char MSG_CHAR_UUID[] =
    "beb5483e-36e1-4688-b7f5-ea07361b26a8";
static constexpr char NEIGHCOUNT_CHAR_UUID[] =
    "beb5483e-36e1-4688-b7f5-ea07361b26a9";

// ATT starts at MTU 23 unless the central negotiates a larger value. The GATT
// notification payload is always the negotiated MTU minus the three-byte ATT
// opcode/handle overhead; merely requesting 247 does not make it effective.
static constexpr uint16_t BLE_DEFAULT_ATT_MTU = 23;
static constexpr uint16_t BLE_REQUESTED_ATT_MTU = 247;
static constexpr uint16_t BLE_MAX_ATT_MTU = 517;
static constexpr size_t BLE_ATT_NOTIFICATION_OVERHEAD = 3u;

constexpr size_t bleNotificationBytesForMtu(uint16_t mtu)
{
    return static_cast<size_t>(
               mtu < BLE_DEFAULT_ATT_MTU
                   ? BLE_DEFAULT_ATT_MTU
                   : (mtu > BLE_MAX_ATT_MTU ? BLE_MAX_ATT_MTU : mtu)) -
           BLE_ATT_NOTIFICATION_OVERHEAD;
}

#pragma pack(push, 1)
struct BLEMessageHeader
{
    uint8_t type;
    uint16_t messageId;
    uint16_t length;
};

struct BLEOutboundMessage
{
    BLEMessageHeader header;
    uint16_t recipientId;
    uint8_t data[];
};

struct BLEInboundMessage
{
    BLEMessageHeader header;
    uint16_t senderId;
    uint8_t data[];
};

struct BLEInboundFragmentMessage
{
    BLEMessageHeader header;
    uint16_t senderId;
    uint16_t totalLength;
    uint16_t offset;
    uint8_t data[];
};

struct BLEAckMessage
{
    BLEMessageHeader header;
    uint16_t originalMessageId;
    uint8_t success;
    uint16_t ping;
};

struct BLENeighborInfo
{
    uint16_t id;
    uint16_t distance;
};

// Route tables are paged so the complete table remains enumerable even when
// the peer keeps the default 23-byte ATT MTU.
struct BLENeighborsMessage
{
    BLEMessageHeader header;
    uint16_t totalCount;
    uint16_t offset;
    uint8_t count;
    BLENeighborInfo neighbors[];
};
#pragma pack(pop)

struct BLEOutboundView
{
    BLEMessageHeader header{};
    uint16_t recipientId = 0;
    const uint8_t *payload = nullptr;
    size_t payloadSize = 0;
};

inline bool parseBLEOutboundMessage(
    const uint8_t *bytes,
    size_t size,
    size_t maximumPayload,
    BLEOutboundView &result)
{
    if (!bytes || size < sizeof(BLEOutboundMessage) || size > UINT16_MAX)
        return false;

    BLEMessageHeader header{};
    memcpy(&header, bytes, sizeof(header));
    if (header.type != BLE_MSG_TYPE_OUTBOUND ||
        header.length != size ||
        header.length < sizeof(BLEOutboundMessage))
        return false;

    const size_t payloadSize = size - sizeof(BLEOutboundMessage);
    if (payloadSize > maximumPayload)
        return false;

    uint16_t recipient = 0;
    memcpy(
        &recipient,
        bytes + sizeof(BLEMessageHeader),
        sizeof(recipient));
    if (recipient == 0)
        return false; // zero is DTPK broadcast, not an application destination

    result.header = header;
    result.recipientId = recipient;
    result.payload = bytes + sizeof(BLEOutboundMessage);
    result.payloadSize = payloadSize;
    return true;
}

constexpr size_t maxBLEInboundPayloadForMtu(uint16_t mtu)
{
    return bleNotificationBytesForMtu(mtu) > sizeof(BLEInboundMessage)
               ? bleNotificationBytesForMtu(mtu) - sizeof(BLEInboundMessage)
               : 0u;
}

constexpr size_t maxBLEInboundFragmentPayloadForMtu(uint16_t mtu)
{
    return bleNotificationBytesForMtu(mtu) >
                   sizeof(BLEInboundFragmentMessage)
               ? bleNotificationBytesForMtu(mtu) -
                     sizeof(BLEInboundFragmentMessage)
               : 0u;
}

constexpr size_t clampBLENeighborCount(size_t count)
{
    return count > UINT8_MAX ? UINT8_MAX : count;
}

constexpr size_t maxBLENeighborsPerNotificationForMtu(uint16_t mtu)
{
    return clampBLENeighborCount(
        bleNotificationBytesForMtu(mtu) > sizeof(BLENeighborsMessage)
            ? (bleNotificationBytesForMtu(mtu) -
               sizeof(BLENeighborsMessage)) /
                  sizeof(BLENeighborInfo)
            : 0u);
}

constexpr size_t maxBLEInboundPayloadPerNotification()
{
    return maxBLEInboundPayloadForMtu(BLE_REQUESTED_ATT_MTU);
}

constexpr size_t maxBLEInboundFragmentPayload()
{
    return maxBLEInboundFragmentPayloadForMtu(BLE_REQUESTED_ATT_MTU);
}

constexpr size_t maxBLENeighborsPerNotification()
{
    return maxBLENeighborsPerNotificationForMtu(BLE_REQUESTED_ATT_MTU);
}

static_assert(sizeof(BLEMessageHeader) == 5, "BLE header wire size changed");
static_assert(sizeof(BLEOutboundMessage) == 7, "BLE outbound prefix changed");
static_assert(sizeof(BLEInboundMessage) == 7, "BLE inbound prefix changed");
static_assert(sizeof(BLEInboundFragmentMessage) == 11,
              "BLE inbound fragment prefix changed");
static_assert(sizeof(BLEAckMessage) == 10, "BLE ACK wire size changed");
static_assert(sizeof(BLENeighborsMessage) == 10,
              "BLE paged-neighbor prefix changed");
static_assert(bleNotificationBytesForMtu(BLE_DEFAULT_ATT_MTU) == 20,
              "default ATT notification capacity changed");
static_assert(maxBLENeighborsPerNotificationForMtu(BLE_DEFAULT_ATT_MTU) == 2,
              "default-MTU neighbor page capacity changed");

#endif
