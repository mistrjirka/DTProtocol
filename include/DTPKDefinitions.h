#ifndef DTPK_DEFINITIONS_H
#define DTPK_DEFINITIONS_H

#include <stdint.h>

// High nibble is the on-wire protocol generation. v3 is intentionally
// incompatible with the historical variable-size route records; old and new
// nodes therefore ignore one another instead of interpreting shifted fields.
static constexpr uint8_t DTPK_WIRE_VERSION = 0x30;
static constexpr uint8_t DTPK_WIRE_VERSION_MASK = 0xf0;

constexpr bool dtpkWireVersionSupported(uint8_t rawType)
{
    return (rawType & DTPK_WIRE_VERSION_MASK) == DTPK_WIRE_VERSION;
}

enum DTPKPacketType : uint8_t
{
    CRYST = DTPK_WIRE_VERSION | 0x00,
    DATA_SINGLE = DTPK_WIRE_VERSION | 0x01,
    ACK = DTPK_WIRE_VERSION | 0x02,
    NACK_NOTFOUND = DTPK_WIRE_VERSION | 0x03,
    HELLO = DTPK_WIRE_VERSION | 0x04,
    CRYST_REQ = DTPK_WIRE_VERSION | 0x05,
    SEQ_REQ = DTPK_WIRE_VERSION | 0x06,
    DATA_FRAGMENT = DTPK_WIRE_VERSION | 0x07,
    FRAGMENT_STATUS = DTPK_WIRE_VERSION | 0x08,
    FRAGMENT_QUERY = DTPK_WIRE_VERSION | 0x09
};

enum DTPKPacketFlags : uint8_t
{
    DTPK_FLAG_NONE = 0,
    DTPK_FLAG_E2E_ACK_REQUESTED = 1 << 0
};

static constexpr uint8_t DTPK_DEFAULT_HOP_LIMIT = 255;
static constexpr uint8_t DTPK_ROUTE_INFINITY = 255;
static constexpr uint8_t DTPK_SEQ_REQ_FLOOD = 1u << 0;

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

// v3 route advertisement. Split horizon is redundant with the
// destination-sequence feasibility invariant, so no next-hop field is sent.
typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t sequence;
    uint8_t distance;
} NeighborRecordV2;

static_assert(sizeof(NeighborRecordV2) == 5,
              "v3 route records must remain five wire bytes");

struct RoutingRecord
{
    uint16_t router;
    uint8_t distance;
    uint16_t sequence;
};

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknown;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    unsigned char data[];
} DTPKPacketGeneric;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
} DTPKPacketHeader;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    uint16_t totalSize;
    uint8_t fragmentIndex;
    unsigned char data[];
} DTPKPacketFragment;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    uint16_t totalSize;
    uint16_t queryId;
} DTPKPacketFragmentQuery;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    uint8_t fragmentCount;
    uint16_t queryId;
    uint8_t missing[];
} DTPKPacketFragmentStatus;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t originSequence;
    uint32_t routeVersion;
    uint16_t chunkIndex;
    uint16_t chunkCount;
    NeighborRecordV2 neighbors[];
} DTPKPacketCryst;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t originSequence;
    uint32_t routeVersion;
} DTPKPacketHello;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    // The digest lets one received HELLO + its reliable request establish the
    // direct route in both directions. It does not affect route metrics.
    uint16_t originSequence;
    uint32_t routeVersion;
} DTPKPacketCrystRequest;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originalSender;
    uint16_t destination;
    uint16_t requestedSequence;
    uint8_t flags;
    uint8_t hopLimit;
} DTPKPacketSeqRequest;

static_assert(sizeof(DTPKPacketType) == 1, "packet type must be one wire byte");
static_assert(sizeof(DTPKPacketCryst) == 11, "v3 CRYST header must be 11 bytes");
static_assert(sizeof(DTPKPacketHello) == 7, "v3 HELLO must be 7 bytes");
static_assert(sizeof(DTPKPacketCrystRequest) == 7,
              "v3 CRYST_REQ must be 7 bytes");
static_assert(sizeof(DTPKPacketFragment) == 14,
              "v3 fragment header must be 14 bytes");
static_assert(sizeof(DTPKPacketFragmentQuery) == 15,
              "v3 fragment query must be 15 bytes");
static_assert(sizeof(DTPKPacketFragmentStatus) == 14,
              "v3 fragment status prefix must be 14 bytes");

#endif
