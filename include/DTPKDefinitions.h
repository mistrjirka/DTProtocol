#include <lcmm.h>

#ifndef DPTKDefinitions_H
#define DPTKDefinitions_H

enum DTPKStates
{
    STANDBY,
    WORKING,
    CRYSTALIZATION
};

enum DTPKPacketType : uint8_t
{
    CRYST,
    DATA_SINGLE,
    ACK,
    NACK_NOTFOUND,
    HELLO,
    CRYST_REQ,
    SEQ_REQ
};

enum DTPKPacketFlags : uint8_t
{
    DTPK_FLAG_NONE = 0,
    DTPK_FLAG_E2E_ACK_REQUESTED = 1 << 0
};

static constexpr uint8_t DTPK_DEFAULT_HOP_LIMIT = 32;
static constexpr uint8_t DTPK_ROUTE_INFINITY = 255;

// Legacy/application-facing view retained for Picopod UI/API compatibility.
typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

// Protocol-v2 route-state record: destination, sender-selected next hop,
// destination generation, and sender's metric to the destination.
typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint16_t sequence;
    uint8_t distance;
} NeighborRecordV2;

typedef struct RoutingRecord
{
    uint16_t router;
    uint16_t originalRouter;
    uint8_t distance;
    uint16_t sequence;
    uint8_t neighborMetric;
} RoutingRecord;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknown;

// DATA/ACK/NACK routed packet prefix.
typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
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
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
} DTPKPacketHeader;

// One complete route-state snapshot may be split into multiple CRYST chunks.
// A receiver applies it transactionally only after all chunks for the same
// (originSequence, routeVersion) have arrived.
typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint16_t routeVersion;
    uint8_t chunkIndex;
    uint8_t chunkCount;
    NeighborRecordV2 neighbors[];
} DTPKPacketCryst;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint16_t routeVersion;
} DTPKPacketHello;

// Direct reliable request asking one neighbour to retransmit its full route
// state. MAC/LCMM targeting identifies which neighbour, so no routed header is
// required.
typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
} DTPKPacketCrystRequest;

// Bounded duplicate-suppressed flood used only when feasibility rejects all
// remaining routes to a destination. The destination advances to at least the
// requested generation and emits normal crystallization state.
typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originalSender;
    uint16_t destination;
    uint16_t requestedSequence;
    uint8_t hopLimit;
} DTPKPacketSeqRequest;

// ---------------------------------------------------------------------------
// Receive layouts include the LCMM/MAC receive prefix.
// ---------------------------------------------------------------------------
typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    uint16_t originalSender;
    uint16_t finalTarget;
    uint8_t flags;
    uint8_t hopLimit;
    unsigned char data[];
} DTPKPacketGenericReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint16_t routeVersion;
    uint8_t chunkIndex;
    uint8_t chunkCount;
    NeighborRecordV2 neighbors[];
} DTPKPacketCrystReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint16_t routeVersion;
} DTPKPacketHelloReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
} DTPKPacketCrystRequestReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    uint16_t originalSender;
    uint16_t destination;
    uint16_t requestedSequence;
    uint8_t hopLimit;
} DTPKPacketSeqRequestReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknownReceive;

// Retained for source compatibility; ACK/NACK use DTPKPacketHeader directly.
typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketHeader header;
    uint16_t responseId;
} DTPKPacketACKReceive;

#endif
