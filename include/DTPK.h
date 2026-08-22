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

class DTPK
{
public:
    // The callback receives only the DTPK payload. The LCMM/MAC transport
    // prefix is intentionally hidden from applications.
    using PacketReceivedCallback =
        std::function<void(DTPKPacketGeneric *packet, uint16_t size)>;
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
        uint16_t sourceSequence;
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

private:
    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataReceive *packet, uint32_t size);
    static void receiveAck(uint16_t id, bool success);

    static constexpr uint32_t HELLO_PERIOD_MS = 10000;
    static constexpr uint32_t MOBILE_HELLO_PERIOD_MS = 4000;
    static constexpr uint32_t HELLO_JITTER_MS = 2000;

    // Missing several HELLOs is only suspicion. Larger half-duplex networks
    // showed healthy neighbors going quiet for >30 s under control load. Probe
    // them directly before deleting their entire routing contribution.
    static constexpr uint32_t NEIGHBOR_SUSPECT_MS = 30000;
    static constexpr uint32_t NEIGHBOR_HARD_EXPIRY_MS = 120000;
    static constexpr uint32_t LIVENESS_PROBE_COOLDOWN_MS = 10000;

    static constexpr uint32_t MAINTENANCE_PERIOD_MS = 1000;
    static constexpr uint32_t CRYST_JITTER_MIN_MS = 200;
    static constexpr uint32_t CRYST_JITTER_MAX_MS = 1500;
    static constexpr uint32_t CRYST_ASSEMBLY_EXPIRY_MS = 30000;
    static constexpr uint32_t SEQ_REQ_RETRY_MIN_MS = 5000;
    static constexpr uint32_t SEQ_REQ_RETRY_MAX_MS = 60000;
    static constexpr uint16_t MAX_CRYST_CHUNKS = 256;
    static constexpr size_t RECENT_DATA_CACHE_SIZE = 64;
    static constexpr size_t RECENT_SEQ_REQ_CACHE_SIZE = 64;

    // Strict duty limiting is optional, but when enabled its legal off-time may
    // exceed the normal hard timeout. It may only lengthen hard expiry.
    uint32_t _neighborHardExpiryMs =
        MAC::getInstance()->recommendedNeighborExpiryMs(
            NEIGHBOR_HARD_EXPIRY_MS,
            HELLO_PERIOD_MS + HELLO_JITTER_MS,
            MAINTENANCE_PERIOD_MS);

    struct PacketIdentity
    {
        // Node id 0 is BROADCAST and cannot originate application DATA, so it
        // is also the empty-entry marker.
        uint16_t originalSender = 0;
        uint16_t sourceSequence = 0;
        uint16_t id = 0;
    };

    struct SeqRequestIdentity
    {
        uint16_t originalSender = 0; // zero marks an unused ring entry
        uint16_t id = 0;
        uint16_t destination = 0;
        uint16_t requestedSequence = 0;
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

    struct ReceivedPacket
    {
        LCMMPacketDataReceive *frame = nullptr;
        size_t dtpkSize = 0;
    };

    std::array<PacketIdentity, RECENT_DATA_CACHE_SIZE> _recentData{};
    size_t _recentDataNext = 0;
    std::array<SeqRequestIdentity, RECENT_SEQ_REQ_CACHE_SIZE> _recentSeqRequests{};
    size_t _recentSeqRequestNext = 0;

    uint32_t _currentTime;
    uint32_t _lastTick;
    uint16_t _packetCounter;
    uint16_t _originSequence;
    uint32_t _routeVersion;
    int32_t _helloRemaining;
    int32_t _maintenanceRemaining;
    bool _mobileHint;

    std::vector<DTPKPacketRequest> _packetRequests;
    std::vector<DTPKPacketWaiting> _packetWaiting;
    std::queue<ReceivedPacket> _packetReceived;
    CrystDatabase _crystDatabase;
    // Negative means no CRYST snapshot is scheduled.
    int32_t _crystRemaining = -1;
    PacketReceivedCallback _recieveCallback;

    std::unordered_map<uint16_t, uint32_t> _lastHeard;
    std::unordered_map<uint16_t, uint32_t> _lastLivenessProbe;
    // LCMM packet id -> direct neighbor. A successful hop ACK is itself proof
    // of liveness even if the subsequent CRYST response is lost.
    std::unordered_map<uint16_t, uint16_t> _livenessProbeByLcmmId;
    std::unordered_map<uint16_t, NeighborState> _neighborState;
    std::unordered_map<uint16_t, CrystAssembly> _crystAssemblies;
    std::unordered_map<uint16_t, PendingSeqRequest> _pendingSeqRequests;

    DTPK(uint16_t originSequence, bool mobileHint);

    bool hasSeenData(uint16_t originalSender, uint16_t sourceSequence,
                     uint16_t id) const;
    void rememberData(uint16_t originalSender, uint16_t sourceSequence,
                      uint16_t id);
    bool hasSeenSeqRequest(uint16_t originalSender, uint16_t id,
                           uint16_t destination, uint16_t requestedSequence) const;
    void rememberSeqRequest(uint16_t originalSender, uint16_t id,
                            uint16_t destination, uint16_t requestedSequence);

    static bool sequenceNewer(uint16_t a, uint16_t b);
    static bool versionNewer(uint32_t a, uint32_t b);
    static bool isControlType(DTPKPacketType type);
    bool hasOutstandingEndToEndAck() const;
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
                        uint16_t sourceSequence,
                        uint16_t failedDestination);
    void sendAckPacket(uint16_t target, uint16_t from, uint16_t id,
                       uint16_t sourceSequence);

    void parseCrystPacket(const ReceivedPacket &packet);
    void parseHelloPacket(const ReceivedPacket &packet);
    void parseCrystRequestPacket(const ReceivedPacket &packet);
    void parseSeqRequestPacket(const ReceivedPacket &packet);
    void parseSingleDataPacket(const ReceivedPacket &packet);
    bool forwardRoutedPacket(const ReceivedPacket &packet);

    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();
    void controlDeamon();
};

#endif
