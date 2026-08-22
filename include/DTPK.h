#ifndef DTPK_H
#define DTPK_H
#include <stdint.h>
#include <functional>
#include <mac.h>
#include <lcmm.h>
#include "generalsettings.h"
#include <array>
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <queue>
#include <mathextension.h>
#include <DTPKDefinitions.h>
#include <CrystDatabase.h>

/**
 * DTPK routing layer.
 *
 * Phase 1 on protocol-v2 makes the current data plane mechanically trustworthy
 * before replacing crystallization semantics. DATA is bounded by a hop limit
 * and a fixed-size replay cache, while ACK/NACK/CRYST control traffic is never
 * blocked by another end-to-end DATA wait.
 */

typedef struct CrystTimeout
{
    int32_t remaining;
    bool sendingPacket;
    int32_t remainingTimeToSend;
} CrystTimeout;

class DTPK
{
public:
    using PacketReceivedCallback =
        std::function<void(DTPKPacketGenericReceive *packet, uint16_t size)>;
    using PacketAckCallback =
        std::function<void(uint8_t result, uint16_t ping)>;

    typedef struct DTPKPacketRequest
    {
        DTPKPacketUnknown *packet;
        size_t size;
        uint16_t target;
        int32_t timeout;
        int32_t timeLeftToSend;
        bool lcmmAck;
        bool dtpkAck;
        DTPK::PacketAckCallback callback;
    } DTPKPacketRequest;

    typedef struct
    {
        uint16_t id;
        int32_t timeLeft;
        uint32_t timeout;
        bool gotAck;
        bool success;
        DTPK::PacketAckCallback callback;
    } DTPKPacketWaiting;

    static DTPK *getInstance();
    static void initialize(uint8_t KLimit = 20);

    void setPacketReceivedCallback(PacketReceivedCallback callback);
    uint16_t sendPacket(uint16_t target, unsigned char *packet, size_t size,
                        int16_t timeout, bool isAck = false,
                        PacketAckCallback callback = nullptr);
    void loop();
    vector<NeighborRecord> getNeighbours();

    static bool isAckPacket(DTPKPacketType type)
    {
        return type == ACK || type == NACK_NOTFOUND;
    }

private:
    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataReceive *packet, uint16_t size);
    static void receiveAck(uint16_t id, bool success);

    struct PacketIdentity
    {
        uint16_t originalSender = 0;
        uint16_t id = 0;
        bool valid = false;
    };

    // Fixed-size replay cache avoids heap churn and naturally ages old 16-bit
    // packet IDs before they can wrap around. It is intentionally DATA-only:
    // duplicate end-to-end ACK/NACK packets are already harmless/idempotent.
    static constexpr size_t RECENT_DATA_CACHE_SIZE = 64;
    std::array<PacketIdentity, RECENT_DATA_CACHE_SIZE> _recentData{};
    size_t _recentDataNext = 0;

    uint64_t _seed;
    uint32_t _timeOfInit;
    uint32_t _currentTime;
    uint32_t _lastTick;
    uint8_t _Klimit;
    uint16_t _packetCounter;

    vector<DTPKPacketRequest> _packetRequests;
    vector<DTPKPacketWaiting> _packetWaiting;
    queue<pair<DTPKPacketUnknownReceive *, size_t>> _packetReceived;
    CrystDatabase _crystDatabase;
    CrystTimeout _crystTimeout;
    PacketReceivedCallback _recieveCallback;
    bool _waitingForAck;
    uint16_t _currentlySendingId;

    bool hasSeenData(uint16_t originalSender, uint16_t id) const;
    void rememberData(uint16_t originalSender, uint16_t id);

    DTPKPacketCryst *prepareCrystPacket(size_t *size);
    bool isPacketForMe(DTPKPacketUnknownReceive *packet, size_t size);

    void addPacketToSendingQueue(DTPKPacketUnknown *packet,
                                 size_t size,
                                 uint16_t target,
                                 int16_t timeout,
                                 int16_t timeLeftToSend,
                                 bool lcmmAck = false,
                                 bool dtpkAck = false,
                                 PacketAckCallback callback = nullptr);

    void sendPacketToTarget(DTPKPacketUnknown *packet,
                            size_t size,
                            uint16_t target,
                            int16_t timeout,
                            bool dtpkAck);

    void sendCrystPacket();
    void sendNackPacket(uint16_t target, uint16_t from, uint16_t id);
    void sendAckPacket(uint16_t target, uint16_t from, uint16_t id);

    void parseCrystPacket(pair<DTPKPacketUnknownReceive *, size_t> packet);
    void parseSingleDataPacket(pair<DTPKPacketUnknownReceive *, size_t> packet);

    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();
    void crystDeamon();

    DTPK(uint8_t KLimit);
};

#endif
