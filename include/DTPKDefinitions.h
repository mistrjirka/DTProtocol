#ifndef DTPK_DEFINITIONS_H
#define DTPK_DEFINITIONS_H

#include <stdint.h>

// High nibble is the on-wire protocol generation. v4 adds transparent
// application-payload compression. A version bump is intentional: a v3 node
// must drop compressed frames rather than hand encoded bytes to its application.
static constexpr uint8_t DTPK_WIRE_VERSION = 0x40;
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
    DTPK_FLAG_E2E_ACK_REQUESTED = 1 << 0,
    DTPK_FLAG_COMPRESSED = 1 << 1,
    // Application-level loop marker used by diagnostic echo nodes. Relays and
    // compression preserve it, while ordinary sendPacket() cannot set it.
    DTPK_FLAG_DEBUG_ECHO = 1 << 2,
    // NACK without this bit means a transient forwarding/route failure and may
    // be retried with the same application identity. This bit means the final
    // destination parsed the complete payload but could not accept it.
    DTPK_FLAG_NACK_FINAL_REJECT = 1 << 3
};

// Applications may set only explicitly assigned application metadata bits.
// Transport-owned ACK/compression bits are derived internally.
static constexpr uint8_t DTPK_APPLICATION_FLAGS_MASK =
    DTPK_FLAG_DEBUG_ECHO;

enum DTPKCompressionCodec : uint8_t
{
    DTPK_COMPRESSION_HEATSHRINK_8_4 = 1
};

typedef struct __attribute__((packed))
{
    uint16_t originalSize;
    uint8_t codec;
    unsigned char data[];
} DTPKCompressedPayload;

static constexpr uint8_t DTPK_DEFAULT_HOP_LIMIT = 255;
static constexpr uint8_t DTPK_ROUTE_INFINITY = 255;
static constexpr uint8_t DTPK_SEQ_REQ_FLOOD = 1u << 0;

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

// v4 route advertisement. Split horizon is redundant with the
// destination-sequence feasibility invariant, so no next-hop field is sent.
typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t sequence;
    uint8_t distance;
} NeighborRecordV2;

static_assert(sizeof(NeighborRecordV2) == 5,
              "v4 route records must remain five wire bytes");

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

// Extended transient-NACK body. `failedRouter` is rewritten by each upstream
// relay to its own node ID, so the source learns which first-hop branch failed.
typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t sourceSequence;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    uint16_t failedRouter;
} DTPKPacketNack;

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
static_assert(sizeof(DTPKCompressedPayload) == 3,
              "v4 compression envelope must remain three bytes");
static_assert(sizeof(DTPKPacketNack) == 13,
              "v4 extended NACK must be 13 bytes");
static_assert(sizeof(DTPKPacketCryst) == 11, "v4 CRYST header must be 11 bytes");
static_assert(sizeof(DTPKPacketHello) == 7, "v4 HELLO must be 7 bytes");
static_assert(sizeof(DTPKPacketCrystRequest) == 7,
              "v4 CRYST_REQ must be 7 bytes");
static_assert(sizeof(DTPKPacketFragment) == 14,
              "v4 fragment header must be 14 bytes");
static_assert(sizeof(DTPKPacketFragmentQuery) == 15,
              "v4 fragment query must be 15 bytes");
static_assert(sizeof(DTPKPacketFragmentStatus) == 14,
              "v4 fragment status prefix must be 14 bytes");

#endif
