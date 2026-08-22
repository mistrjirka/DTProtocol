#ifndef DTPK_H
#define DTPK_H

#include <stdint.h>
#include <array>
#include <functional>
#include <queue>
#include <unordered_map>
#include <vector>

#include <mac.h>
#include <lcmm.h>
#include <mathextension.h>
#include <DTPKDefinitions.h>
#include <CrystDatabase.h>

struct CrystTimeout
{
    bool sendingPacket;
    int32_t remainingTimeToSend;
};

class DTPK
{
public:
    using PacketReceivedCallback =
        std::function<void(DTPKPacketGenericReceive *packet, uint16_t size)>;
    using PacketAckCallback =
        std::function<void(uint8_t result, uint16_t ping)>;

    struct DTPKPacketRequest
    {
        DTPKPacketUnknown *packet;
        size_t size;
        uint16_t target;
        int32_t timeout;
        int32_t timeLeftToSend;
        bool lcmmAck;
        bool dtpkAck;
        PacketAckCallback callback;
    };

    struct DTPKPacketWaiting
    {
        uint16_t id;
        uint16_t target;
        int32_t timeLeft;
        uint32_t timeout;
        bool gotAck;
        bool success;
        PacketAckCallback callback;
    };

    static DTPK *getInstance();
    static void initialize(
        uint8_t KLimit = 20,
        uint16_t originSequence = 1,
        bool mobileHint = false);

    void setPacketReceivedCallback(PacketReceivedCallback callback);
    uint16_t sendPacket(uint16_t target, unsigned char *packet, size_t size,
                        int16_t timeout, bool isAck = false,
                        PacketAckCallback callback = nullptr);
    void loop();
    std::vector<NeighborRecord> getNeighbours();
    bool isMobileHintEnabled() const { return _mobileHint; }

    static bool isAckPacket(DTPKPacketType type)
    {
        return type == ACK || type == NACK_NOTFOUND;
    }

private:
    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataReceive *packet, uint32_t size);
    static void receiveAck(uint16_t id, bool success);

    static constexpr uint32_t HELLO_PERIOD_MS = 10000;
    static constexpr uint32_t MOBILE_HELLO_PERIOD_MS = 4000;
    static constexpr uint32_t HELLO_JITTER_MS = 2000;
    static constexpr uint32_t BASE_NEIGHBOR_EXPIRY_MS = 30000;
    static constexpr uint32_t MAINTENANCE_PERIOD_MS = 1000;
    static constexpr uint32_t CRYST_JITTER_MIN_MS = 200;
    static constexpr uint32_t CRYST_JITTER_MAX_MS = 1500;
    static constexpr uint32_t CRYST_ASSEMBLY_EXPIRY_MS = 30000;
    static constexpr uint32_t SEQ_REQ_COOLDOWN_MS = 5000;
    static constexpr uint8_t SEQ_REQ_MAX_ATTEMPTS = 5;
    static constexpr uint16_t MAX_CRYST_CHUNKS = 256;
    static constexpr size_t RECENT_DATA_CACHE_SIZE = 64;
    static constexpr size_t RECENT_SEQ_REQ_CACHE_SIZE = 64;

    // Practical/default MAC has no duty throttle, so this remains 30 s. If an
    // application explicitly enables strict duty limiting before DTPK starts,
    // this helper keeps that optional mode from breaking liveness.
    uint32_t NEIGHBOR_EXPIRY_MS =
        MAC::getInstance()->recommendedNeighborExpiryMs(
            BASE_NEIGHBOR_EXPIRY_MS,
            HELLO_PERIOD_MS + HELLO_JITTER_MS,
            MAINTENANCE_PERIOD_MS);

    struct PacketIdentity
    {
        uint16_t originalSender = 0;
        uint16_t id = 0;
        bool valid = false;
    };

    struct SeqRequestIdentity
    {
        uint16_t originalSender = 0;
        uint16_t id = 0;
        uint16_t destination = 0;
        uint16_t requestedSequence = 0;
        bool valid = false;
    };

    struct NeighborState
    {
        uint16_t originSequence = 0;
        uint32_t routeVersion = 0;
    };

    struct CrystAssembly
    {
        uint16_t originSequence = 0;
        uint32_t routeVersion = 0;
        uint16_t chunkCount = 0;
        uint32_t lastUpdate = 0;
        std::vector<std::vector<NeighborRecordV2>> chunks;
        std::vector<uint8_t> received;
    };

    struct PendingSeqRequest
    {
        uint16_t requestedSequence = 0;
        uint32_t lastSent = 0;
        uint8_t attempts = 0;
    };

    std::array<PacketIdentity, RECENT_DATA_CACHE_SIZE> _recentData{};
    size_t _recentDataNext = 0;
    std::array<SeqRequestIdentity, RECENT_SEQ_REQ_CACHE_SIZE> _recentSeqRequests{};
    size_t _recentSeqRequestNext = 0;

    uint64_t _seed;
    uint32_t _timeOfInit;
    uint32_t _currentTime;
    uint32_t _lastTick;
    uint8_t _Klimit;
    uint16_t _packetCounter;
    uint16_t _originSequence;
    uint32_t _routeVersion;
    int32_t _helloRemaining;
    int32_t _maintenanceRemaining;
    bool _mobileHint;

    std::vector<DTPKPacketRequest> _packetRequests;
    std::vector<DTPKPacketWaiting> _packetWaiting;
    std::queue<std::pair<DTPKPacketUnknownReceive *, size_t>> _packetReceived;
    CrystDatabase _crystDatabase;
    CrystTimeout _crystTimeout;
    PacketReceivedCallback _recieveCallback;
    bool _waitingForAck;
    uint16_t _currentlySendingId;

    std::unordered_map<uint16_t, uint32_t> _lastHeard;
    std::unordered_map<uint16_t, NeighborState> _neighborState;
    std::unordered_map<uint16_t, CrystAssembly> _crystAssemblies;
    std::unordered_map<uint16_t, PendingSeqRequest> _pendingSeqRequests;

    DTPK(uint8_t KLimit, uint16_t originSequence, bool mobileHint);

    bool hasSeenData(uint16_t originalSender, uint16_t id) const;
    void rememberData(uint16_t originalSender, uint16_t id);
    bool hasSeenSeqRequest(uint16_t originalSender, uint16_t id,
                           uint16_t destination, uint16_t requestedSequence) const;
    void rememberSeqRequest(uint16_t originalSender, uint16_t id,
                            uint16_t destination, uint16_t requestedSequence);

    static bool sequenceNewer(uint16_t a, uint16_t b);
    static bool versionNewer(uint32_t a, uint32_t b);
    static bool isControlType(DTPKPacketType type);
    uint16_t nextPacketId();
    uint32_t effectiveHelloPeriodMs() const;

    void markRoutingChanged(const char *reason);
    void noteHeard(uint16_t neighbor);
    void expireNeighbours();
    void expireAssemblies();
    void processSequenceRequests();
    void retrySequenceRequests();

    void addPacketToSendingQueue(DTPKPacketUnknown *packet,
                                 size_t size,
                                 uint16_t target,
                                 int16_t timeout,
                                 int16_t timeLeftToSend,
                                 bool lcmmAck = false,
                                 bool dtpkAck = false,
                                 PacketAckCallback callback = nullptr,
                                 bool priority = false);

    void sendCrystPacket();
    void queueCrystSnapshot();
    void sendHello();
    void sendCrystRequest(uint16_t neighbor);
    void sendSeqRequest(uint16_t destination, uint16_t requestedSequence);
    void sendNackPacket(uint16_t target, uint16_t from, uint16_t id,
                        uint16_t failedDestination);
    void sendAckPacket(uint16_t target, uint16_t from, uint16_t id);

    void parseCrystPacket(std::pair<DTPKPacketUnknownReceive *, size_t> packet);
    void parseHelloPacket(std::pair<DTPKPacketUnknownReceive *, size_t> packet);
    void parseCrystRequestPacket(std::pair<DTPKPacketUnknownReceive *, size_t> packet);
    void parseSeqRequestPacket(std::pair<DTPKPacketUnknownReceive *, size_t> packet);
    void parseSingleDataPacket(std::pair<DTPKPacketUnknownReceive *, size_t> packet);
    bool forwardRoutedPacket(DTPKPacketUnknownReceive *packet, size_t size);

    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();
    void controlDeamon();
};

#endif
