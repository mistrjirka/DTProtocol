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

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

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

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint32_t routeVersion;
    uint16_t chunkIndex;
    uint16_t chunkCount;
    NeighborRecordV2 neighbors[];
} DTPKPacketCryst;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint32_t routeVersion;
} DTPKPacketHello;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
} DTPKPacketCrystRequest;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    uint16_t originalSender;
    uint16_t destination;
    uint16_t requestedSequence;
    uint8_t hopLimit;
} DTPKPacketSeqRequest;

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
    uint32_t routeVersion;
    uint16_t chunkIndex;
    uint16_t chunkCount;
    NeighborRecordV2 neighbors[];
} DTPKPacketCrystReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    uint16_t originSequence;
    uint32_t routeVersion;
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

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketHeader header;
    uint16_t responseId;
} DTPKPacketACKReceive;

#endif
