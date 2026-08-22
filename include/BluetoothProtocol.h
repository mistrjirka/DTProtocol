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

// The ESP32 server requests MTU 247. ATT notifications then have at most
// MTU-3 bytes of application payload.
static constexpr uint16_t BLE_REQUESTED_MTU = 247;
static constexpr size_t BLE_MAX_NOTIFICATION_BYTES = BLE_REQUESTED_MTU - 3u;

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

struct BLENeighborsMessage
{
    BLEMessageHeader header;
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
    if (!bytes || size < sizeof(BLEOutboundMessage))
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

constexpr size_t maxBLEInboundPayloadPerNotification()
{
    return BLE_MAX_NOTIFICATION_BYTES - sizeof(BLEInboundMessage);
}

constexpr size_t maxBLEInboundFragmentPayload()
{
    return BLE_MAX_NOTIFICATION_BYTES - sizeof(BLEInboundFragmentMessage);
}

constexpr size_t maxBLENeighborsPerNotification()
{
    return (BLE_MAX_NOTIFICATION_BYTES - sizeof(BLENeighborsMessage)) /
           sizeof(BLENeighborInfo);
}

static_assert(sizeof(BLEMessageHeader) == 5, "BLE header wire size changed");
static_assert(sizeof(BLEOutboundMessage) == 7, "BLE outbound prefix changed");
static_assert(sizeof(BLEInboundMessage) == 7, "BLE inbound prefix changed");
static_assert(sizeof(BLEInboundFragmentMessage) == 11,
              "BLE inbound fragment prefix changed");
static_assert(sizeof(BLEAckMessage) == 10, "BLE ACK wire size changed");
static_assert(sizeof(BLENeighborsMessage) == 6, "BLE neighbor prefix changed");
static_assert(maxBLENeighborsPerNotification() == 59,
              "MTU-247 neighbor capacity changed");

#endif
