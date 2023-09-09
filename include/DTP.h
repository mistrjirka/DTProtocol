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
#define DTP_PACKET_TYPE_DATA_SINGLE 1
#define DTP_PACKET_TYPE_ACK 2
#define DTP_PACKET_TYPE_NACK_NOTFOUND 3
#define DTP_PACKET_TYPE_NACK_TIMEOUT 4
#define DTP_PACKET_TYPE_NACK_REFUSED 5

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
    uint8_t type; // 0 = NAP, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t originalSender;
    uint16_t finalTarget;
    uint16_t id;
} DTPPacketHeader;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    uint8_t type; // 0 = NAP, 1 = DATA, 2 = ACK, 3 = NACK - target not in database, 4 = NACK - target unresponsive
    uint16_t originalSender;
    uint16_t finalTarget;
    uint16_t id;
    unsigned char data[];
} DTPPacketGenericRecieve;

typedef struct __attribute__((packed))
{
    uint8_t type;
    NeighborRecord neighbors[];
} DTPPacketNAP;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    uint8_t type;
    NeighborRecord neighbors[];
} DTPPacketNAPRecieve;

typedef struct __attribute__((packed))
{
    LCMMDataHeader lcmm;
    DTPPacketHeader header;
    uint16_t responseId;
} DTPPacketACKRecieve;

typedef struct __attribute__((packed))
{
    DTPPacketHeader header;
    uint16_t responseId;
} DTPPacketACK;

typedef struct
{
    uint8_t distance;
} DTPRoutingItem;

typedef struct
{
    uint16_t id;
    uint8_t distance;
    bool sharable;
} DTPRoutingItemFull;

typedef struct
{
    uint16_t id;
    uint32_t startTime;
    uint32_t endTime;
    uint32_t lastIntervalHeard;
    unordered_map<uint16_t, DTPRoutingItem> routes;
} DTPNAPTimeRecord;

typedef struct
{
    uint32_t startTime;
    uint32_t endTime;
} DTPNAPTimeRecordSimple;

typedef struct
{
    bool ack;
    uint16_t target;
    unsigned char *data;
    uint8_t size;
    uint16_t timeout;
} DTPSendRequest;

class DTP
{
public:
    // Callback function type definition
    using PacketReceivedCallback =
        std::function<void(DTPPacketGenericRecieve *packet, uint16_t size)>;
    using PacketAckCallback =
        std::function<void(uint8_t result)>;
    typedef struct
    {
        uint16_t id;
        uint16_t timeLeft;
        uint16_t timeout;
        DTP::PacketAckCallback callback;
    } DTPPacketWaiting;
    static DTP *getInstance();

    static void initialize(uint8_t NAPInterval = 30);
    void loop();
    DTPStates getState();
    DTPNAPTimeRecord getMyNAP();
    void setPacketRecievedCallback(PacketReceivedCallback fun);
    uint16_t sendPacket(uint8_t *data, uint8_t size, uint16_t target, uint16_t timeout, DTP::PacketAckCallback callback);
    unordered_map<uint16_t, DTPRoutingItemFull> getRoutingTable();
    vector<DTPNAPTimeRecord> neighbors();

private:
    static bool neighborPacketWaiting;
    static bool dataPacketWaiting;
    static bool ackPacketWaiting;
    static DTPPacketACKRecieve *ackPacketToParse;
    static DTPPacketGenericRecieve *dataPacketToParse;
    static DTPPacketNAPRecieve *neighborPacketToParse;
    static uint16_t dtpPacketSize;
    static uint16_t dataPacketSize;
    uint32_t timeOfInit;
    uint16_t currentTime;
    uint8_t NAPInterval;
    uint32_t numOfIntervalsElapsed;
    uint32_t lastNAPSsentInterval;
    uint32_t packetIdCounter;
    uint32_t lastTick;
    bool NAPPlaned;
    bool NAPSend;
    static bool sending;
    DTPNAPTimeRecord myNAP;
    PacketReceivedCallback recieveCallback;

    DTPStates state;
    vector<DTPPacketWaiting> waitingForAck;
    vector<DTPNAPTimeRecord> activeNeighbors;
    vector<uint16_t> waitingToBeConfirmedPackets;
    unordered_map<uint16_t, DTPNAPTimeRecord> previousActiveNeighbors;

    unordered_map<uint16_t, DTPRoutingItemFull> routingCacheTable;
    vector<DTPSendRequest> sendingQueue;
    size_t sizeOfRouting();

    static DTP *dtp;

    DTP(uint8_t NAPInterval);

    uint32_t getTimeOnAirOfNAP();
    uint16_t getRoutingItem(uint16_t id);
    DTPNAPTimeRecordSimple getNearestTimeSlot(uint32_t ideal_min_time, uint32_t ideal_max_time, uint32_t min_start_time, uint32_t max_end_time);
    bool checkIfCurrentPlanIsColliding();

    void parseNeigbours();
    void parseDataPacket();
    void parseAckPacket();
    void sendNAP();
    void sendNAPPacket();
    void updateTime();
    void NAPPlanRandom();
    void NAPPlanInteligent();
    void cleaningDeamon();
    void timeoutDeamon();
    void sendingDeamon();
    void sendingFront();
    void savePreviousActiveNeighbors();
    void addPacketToSendingQueue(bool needACK, uint16_t target,
                                unsigned char *data, uint8_t size,
                                uint32_t timeout);

    static void receivePacket(LCMMPacketDataRecieve *packet, uint16_t size);
    static void receiveAck(uint16_t id, bool success);
};

#endif // DTP_H