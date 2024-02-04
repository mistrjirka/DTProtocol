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

    DTPKPacketCryst *prepareCrystPacket(size_t *size);

    void addPacketToSendingQueue(DTPKPacketGeneric *packet, size_t size, uint16_t target, int16_t timeout, int16_t timeLeftToSend, bool isAck = false, PacketAckCallback callback = nullptr);
    void sendCrystPacket();
    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();

    
    DTPK(uint8_t KLimit);
};

#endif