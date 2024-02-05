#ifndef DTPK_H
#define DTPK_H
#include <stdint.h>
#include <functional>
#include <mac.h>
#include <lcmm.h>
#include "generalsettings.h"
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <queue>
#include <mathextension.h>
#include <DPTKDefinitions.h>
#include <CrystDatabase.h>

/**
 * Axiomatical definition of protocol.
 * Let node be a device that is capable of sending and receiving packets with these properties:
 * If node recieves crystalization packet
 * - It will check if sender is in routing table. If not, it will add sender to the routing table with all of its children and update the routing cache.
 * - If routing cache changes, it will send crystalization packet to all of its neighbours.
 * - If node is not in crystalization session, it will start one.
 * - If node is in crystalization session, it will add sender to the list of nodes in crystalization session.
 * - If timeout of Klimit is reached, it will end crystalization session and remove all nodes that are not in crystalization session from routing table.
 * - - If routing table changes, it will send crystalization packet to all of its neighbours.
 * **/


typedef struct DTPKPacketRequest 
{
    DTPKPacketGeneric *packet;
    size_t size;
    uint16_t target;
    int16_t timeout;
    int16_t timeLeftToSend;
    bool isAck;
    DTPK::PacketAckCallback callback;
};

typedef struct CrystTimeout 
{
    int remaining;
};

class DTPK
{
    public:
    // Callback function type definition
    using PacketReceivedCallback =
        std::function<void(DTPKPacketGenericReceive *packet, uint16_t size)>;
    using PacketAckCallback =
        std::function<void(uint8_t result, uint16_t ping)>;

    typedef struct
    {
        uint16_t id;
        uint16_t timeLeft;
        uint16_t timeout;
        bool gotAck;
        bool success;
        DTPK::PacketAckCallback callback;
    } DTPKPacketWaiting;

    static DTPK *getInstance();

    static void initialize(uint8_t KLimit = 20);
    void setPacketReceivedCallback(PacketReceivedCallback callback);
    uint16_t sendPacket(uint16_t target, unsigned char *packet, size_t size, int16_t timeout, bool isAck = false, PacketAckCallback callback = nullptr);
    void loop();

    private:

    //Static section

    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataReceive *packet, uint16_t size);
    static void receiveAck(uint16_t id, bool success);

    //Dynamic section

    uint64_t seed;
    uint64_t timeOfInit;
    uint64_t currentTime;
    uint64_t lastTick;
    uint8_t Klimit;
    uint16_t packetCounter;
    vector<DTPKPacketRequest> packetRequests;
    vector<DTPKPacketWaiting> packetWaiting;
    queue<pair<DTPKPacketGenericReceive*, size_t>> packetReceived;
    CrystDatabase crystDatabase;
    CrystTimeout crystTimeout;
    PacketReceivedCallback recieveCallback;


    DTPKPacketCryst *prepareCrystPacket(size_t *size);

    void addPacketToSendingQueue(DTPKPacketGeneric *packet, size_t size, uint16_t target, int16_t timeout, int16_t timeLeftToSend, bool isAck = false, PacketAckCallback callback = nullptr);
    void sendCrystPacket();
    void sendNackPacket(uint16_t target, uint16_t id);
    void sendAckPacket(uint16_t target, uint16_t id);

    void parseCrystPacket(pair<DTPKPacketGenericReceive*, size_t> packet);
    void parseSingleDataPacket(pair<DTPKPacketGenericReceive*, size_t> packet);

    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();
    void crystTimeoutDeamon();

    
    DTPK(uint8_t KLimit);
};

#endif