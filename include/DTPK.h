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


typedef struct DTPKPacketRequest 
{
    DTPKPacketGeneric packet;
    int16_t timeout;
    int16_t timeLeftToSend;
};

class DTPK
{
    public:
    // Callback function type definition
    using PacketReceivedCallback =
        std::function<void(DTPKPacketGenericRecieve *packet, uint16_t size)>;
    using PacketAckCallback =
        std::function<void(uint8_t result, uint16_t ping)>;

    typedef struct
    {
        uint16_t id;
        uint16_t timeLeft;
        uint16_t timeout;
        DTPK::PacketAckCallback callback;
    } DTPKPacketWaiting;

    static DTPK *getInstance();

    static void initialize(uint8_t KLimit = 20);
    void loop();

    DTPK *DTPK::getInstance();

    private:

    //Static section

    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataRecieve *packet, uint16_t size);
    static void receiveAck(uint16_t id, bool success);

    //Dynamic section

    uint64_t seed;
    uint64_t timeOfInit;
    uint64_t currentTime;
    uint8_t Klimit;
    std::vector<DTPKPacketRequest> packetRequests;
    std::queue<DTPKPacketRequest> packetRecieved;

    DTPKPacketCryst *prepeareCrystPacket();
    DTPK(uint8_t KLimit);
    

       
};

#endif