#ifndef DTP_H
#define DTP_H
#include <stdint.h>
#include <functional>
#include <mac.h>
#include <lcmm.h>
#include "generalsettings.h"
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <mathextension.h>
#define DTP_PACKET_TYPE_NAP 0

enum DTPStates
{
    STANDBY,
    WORKING,
    INITIAL_LISTENING
};

typedef struct __attribute__((packed))
{
    uint16_t id;
    uint8_t distance;
} NeighborRecord;

typedef struct __attribute__((packed))
{
    uint8_t type; // 0 = NAP, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    unsigned char data[];
} DTPPacketUnkown;

typedef struct __attribute__((packed))
{
    uint8_t type; // 0 = NAP, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t originalSender;
    uint16_t finalTarget;
    uint16_t id;
    unsigned char data[];
} DTPPacketGeneric;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    uint8_t type;// 0 = NAP, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t originalSender;
    uint16_t finalTarget;
    uint16_t id;
    unsigned char data[];
} DTPPacketGenericRecieve;

typedef struct __attribute__((packed))
{
    uint8_t type;
    NeighborRecord *neighbors;
} DTPPacketNAP;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    uint8_t type;
    NeighborRecord *neighbors;
} DTPPacketNAPRecieve;

typedef struct __attribute__((packed))
{
    uint16_t *proxies;
} DTPPacketNACK;

typedef struct
{
    uint16_t routingId;
    uint8_t distance;
} DTPRoutingItem;

typedef struct
{
    uint16_t id;
    uint32_t startTime;
    uint32_t endTime;
    uint32_t lastIntervalHeard;
} DTPNAPTimeRecord;

class DTP
{
public:
    // Callback function type definition
    using PacketReceivedCallback =
        std::function<void(MACPacket *packet, uint16_t size, uint32_t crcCalculated)>;
    static DTP *getInstance();

    static void initialize(uint16_t id, uint8_t NAPInterval = 30);
    void loop();
    DTPStates getState();

private:
    static bool neighborPacketWaiting;
    static DTPPacketNAPRecieve *neighborPacketToParse;
    static uint16_t dtpPacketSize;
    uint32_t timeOfInit;
    uint16_t currentTime;
    uint8_t NAPInterval;
    uint32_t numOfIntervalsElapsed;
    uint32_t lastNAPSsentInterval;
    bool NAPPlaned;
    bool NAPSend;
    DTPNAPTimeRecord myNAP;
    
    uint16_t id;

    DTPStates state;
    vector<DTPNAPTimeRecord> activeNeighbors;
    unordered_map<uint16_t, vector<DTPRoutingItem>> routingTable;


    static DTP *dtp;

    DTP(uint16_t id, uint8_t NAPInterval);

    uint32_t getTimeOnAirOfNAP();
    DTPNAPTimeRecord getNearestTimeSlot(uint32_t ideal_min_time, uint32_t ideal_max_time, uint32_t min_start_time, uint32_t max_end_time);
    bool checkIfCurrentPlanIsColliding();

    void parseNeigbours();
    void sendNAP();
    void sendNAPPacket();
    void updateTime();
    void NAPPlanRandom();
    void NAPPlanInteligent();
    void cleaningDeamon();

    static void receivePacket(LCMMPacketDataRecieve *packet, uint16_t size);
    static void receiveAck(uint16_t id, bool success);
};

#endif // DTP_H