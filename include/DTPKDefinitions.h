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
    NACK_NOTFOUND
};

// Generic routed-packet flags. These are part of the protocol-v2 wire format.
enum DTPKPacketFlags : uint8_t
{
    DTPK_FLAG_NONE = 0,
    DTPK_FLAG_E2E_ACK_REQUESTED = 1 << 0
};

// This is deliberately independent of route distance/infinity. It is a packet
// forwarding safety bound: even an implementation or transient routing bug
// cannot circulate one payload forever.
static constexpr uint8_t DTPK_DEFAULT_HOP_LIMIT = 32;

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

typedef struct RoutingRecord
{
    uint16_t router;
    uint16_t originalRouter;
    uint8_t distance;
} RoutingRecord;

typedef struct __attribute__((packed))
{
    DTPKPacketType type;
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknown;

// All routed packets (DATA/ACK/NACK) share this prefix. CRYST deliberately keeps
// its compact existing header because it is broadcast rather than routed.
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
    NeighborRecord neighbors[];
} DTPKPacketCryst;

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
    NeighborRecord neighbors[];
} DTPKPacketCrystReceive;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketType type;
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknownReceive;

// Retained for compatibility with code that refers to this type. ACK/NACK now
// use DTPKPacketHeader directly and do not carry a second response ID.
typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPKPacketHeader header;
    uint16_t responseId;
} DTPKPacketACKReceive;

#endif
