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

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint16_t from;
    uint8_t distance;
} NeighborRecord;

typedef struct RoutingRecord {
    uint16_t router;
    uint16_t originalRouter;
    uint8_t distance;

} RoutingRecord;

typedef struct __attribute__((packed))
{
    DTPKPacketType type; // 0 = Cryst, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t id;
    unsigned char data[];
} DTPKPacketUnknown;


typedef struct __attribute__((packed))
{
    DTPKPacketType type; // 0 = Cryst, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t id;
    uint16_t originalSender;
    uint16_t finalTarget;
    unsigned char data[];
} DTPKPacketGeneric;

typedef struct __attribute__((packed))
{
    DTPKPacketType type; // 0 = Cryst, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t id;
    uint16_t originalSender;
    uint16_t finalTarget;
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
    DTPKPacketType type; // 0 = Cryst, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t id;
    uint16_t originalSender;
    uint16_t finalTarget;
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
    DTPKPacketType type; // 0 = Cryst, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
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