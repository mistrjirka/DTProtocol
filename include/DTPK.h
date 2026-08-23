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

    struct CompressionDiagnostics
    {
        uint32_t attempts = 0;
        uint32_t selected = 0;
        uint32_t noAirtimeBenefit = 0;
        uint32_t candidateTooLarge = 0;
        uint32_t codecFailures = 0;
        uint32_t verificationFailures = 0;
        uint32_t decodeFailures = 0;
        uint32_t allocationFailures = 0;
        uint64_t originalBytes = 0;
        uint64_t encodedBytes = 0;
        uint64_t estimatedAirtimeSavedMs = 0;
    };

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
        uint8_t queueClass;
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
    static bool initialize(
        uint8_t KLimit,
        uint16_t originSequence,
        bool mobileHint = false);

    void setPacketReceivedCallback(PacketReceivedCallback callback);
    uint16_t sendPacket(uint16_t target, unsigned char *packet, size_t size,
                        int32_t timeout, bool isAck = false,
                        PacketAckCallback callback = nullptr);
    void loop();
    std::vector<NeighborRecord> getNeighbours();
    bool isMobileHintEnabled() const { return _mobileHint; }
    const CompressionDiagnostics &getCompressionDiagnostics() const
    {
        return _compressionDiagnostics;
    }

    static constexpr size_t maximumSinglePayloadSize()
    {
        return DATASIZE_LCMM - sizeof(DTPKPacketGeneric);
    }
    static constexpr size_t fragmentPayloadSize()
    {
        return DATASIZE_LCMM - sizeof(DTPKPacketFragment);
    }
    static constexpr size_t maximumMessageSize()
    {
        return DTPK_MAX_MESSAGE_SIZE < fragmentPayloadSize() * 255u
                   ? DTPK_MAX_MESSAGE_SIZE
                   : fragmentPayloadSize() * 255u;
    }

private:
    static DTPK *dtpk;
    static void receivePacket(LCMMPacketDataReceive *packet, uint32_t size);
    static void receiveAck(uint16_t id, bool success);

    static constexpr uint32_t HELLO_PERIOD_MS = 10000;
    static constexpr uint32_t MOBILE_HELLO_PERIOD_MS = 4000;
    static constexpr uint32_t MOBILE_DISCOVERY_HELLO_PERIOD_MS = 1000;
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
    static constexpr uint16_t MAX_CRYST_CHUNKS = 16;
    static constexpr size_t RECENT_DATA_CACHE_SIZE = 64;
    static constexpr size_t RECENT_SEQ_REQ_CACHE_SIZE = 64;
    static constexpr uint8_t MAX_REPAIR_BURST = 4;
    static constexpr uint8_t MAX_FRAGMENT_COUNT = 255;
    static constexpr uint32_t FRAGMENT_QUERY_INTERVAL_MS = 5000;
    static constexpr uint32_t FRAGMENT_ASSEMBLY_EXPIRY_MS = 120000;
    // DTPK gives a source fragment a 10 s request budget and relayed packets a
    // 5 s request budget; LCMM divides that into five retry attempts. These
    // conservative wall-clock budgets include retry jitter and airtime.
    static constexpr uint32_t FRAGMENT_SOURCE_HOP_BUDGET_MS = 20000;
    static constexpr uint32_t FRAGMENT_RELAY_HOP_BUDGET_MS = 10000;
    static constexpr size_t FRAGMENT_BITMAP_BYTES =
        (MAX_FRAGMENT_COUNT + 7u) / 8u;
    static_assert(DTPK_MAX_FRAGMENT_ASSEMBLIES > 0,
                  "at least one fragment assembly slot is required");
    static constexpr uint8_t TX_NORMAL = 0;
    static constexpr uint8_t TX_REPAIR = 1;
    static constexpr uint8_t TX_RESPONSE = 2;

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
        uint16_t originSequence;
        uint32_t routeVersion;

        NeighborState() : originSequence(0), routeVersion(0) {}
        NeighborState(uint16_t sequence, uint32_t version)
            : originSequence(sequence), routeVersion(version) {}
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
        uint16_t requestedSequence;
        uint32_t lastSent;
        uint16_t attempts;

        PendingSeqRequest()
            : requestedSequence(0), lastSent(0), attempts(0) {}
        PendingSeqRequest(uint16_t sequence, uint32_t sent, uint16_t count)
            : requestedSequence(sequence), lastSent(sent), attempts(count) {}
    };

    struct MultipartSend
    {
        bool active = false;
        uint16_t id = 0;
        uint16_t sourceSequence = 0;
        uint16_t target = 0;
        uint16_t totalSize = 0;
        uint8_t flags = DTPK_FLAG_NONE;
        uint8_t fragmentCount = 0;
        uint8_t nextInitialFragment = 0;
        uint32_t nextQueryAt = 0;
        uint16_t nextQueryId = 1;
        uint16_t lastStatusQueryId = UINT16_MAX;
        uint8_t *payload = nullptr;
        std::array<uint8_t, MAX_FRAGMENT_COUNT> retransmit{};
    };

    struct FragmentAssembly
    {
        bool active = false;
        uint16_t id = 0;
        uint16_t sourceSequence = 0;
        uint16_t originalSender = 0;
        uint16_t totalSize = 0;
        uint16_t lastHop = 0;
        uint8_t flags = 0;
        uint8_t fragmentCount = 0;
        uint8_t receivedCount = 0;
        uint32_t lastUpdate = 0;
        uint32_t lastStatus = 0;
        uint8_t *packetBuffer = nullptr;
        std::array<uint8_t, FRAGMENT_BITMAP_BYTES> received{};
    };

    struct ReceivedPacket
    {
        LCMMPacketDataReceive *frame;
        size_t dtpkSize;

        ReceivedPacket() : frame(nullptr), dtpkSize(0) {}
        ReceivedPacket(LCMMPacketDataReceive *value, size_t size)
            : frame(value), dtpkSize(size) {}
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
    uint8_t _repairBurst = 0;
    std::vector<DTPKPacketWaiting> _packetWaiting;
    std::queue<ReceivedPacket> _packetReceived;
    CrystDatabase _crystDatabase;
    // Negative means no CRYST snapshot is scheduled.
    int32_t _crystRemaining = -1;
    PacketReceivedCallback _recieveCallback;

    std::unordered_map<uint16_t, uint32_t> _lastHeard;
    std::unordered_map<uint16_t, uint32_t> _lastLivenessProbe;
    // LCMM packet id -> direct next hop. Every successful reliable-unicast ACK
    // is positive bidirectional liveness evidence, not only explicit probes.
    std::unordered_map<uint16_t, uint16_t> _directAckNeighborByLcmmId;
    // Source-side fragment/query LCMM transactions. Their completion starts
    // the downstream end-to-end status wait; queue time is too early on a busy
    // multi-hop path.
    std::unordered_map<uint16_t, uint8_t> _multipartLcmmIds;
    std::unordered_map<uint16_t, NeighborState> _neighborState;
    std::unordered_map<uint16_t, CrystAssembly> _crystAssemblies;
    std::unordered_map<uint16_t, PendingSeqRequest> _pendingSeqRequests;
    MultipartSend _multipartSend;
    CompressionDiagnostics _compressionDiagnostics;
    std::array<FragmentAssembly, DTPK_MAX_FRAGMENT_ASSEMBLIES>
        _fragmentAssemblies{};

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
    bool hasKnownDirectNeighbor() const;

    void markRoutingChanged(const char *reason);
    void noteHeard(uint16_t neighbor);
    void expireNeighbours();
    void expireAssemblies();
    void processSequenceRequests();
    void retrySequenceRequests();
    void maintainFragmentAssemblies();
    void pumpMultipartSend();
    void clearMultipartSend();
    uint8_t fragmentCountForSize(size_t size) const;
    size_t expectedFragmentBytes(uint16_t totalSize, uint8_t index) const;
    uint32_t multipartQueryDelayMs();
    uint64_t estimatedReliablePayloadAirtimeMs(size_t payloadSize) const;
    bool tryCompressPayload(
        const uint8_t *payload, size_t size,
        uint8_t *&encoded, size_t &encodedSize);
    bool deliverApplicationPacket(
        DTPKPacketGeneric *packet, size_t packetSize);
    bool queueMultipartFragment(uint8_t index);
    bool queueFragmentQuery();
    FragmentAssembly *findFragmentAssembly(
        uint16_t originalSender, uint16_t sourceSequence, uint16_t id);
    FragmentAssembly *allocateFragmentAssembly(
        uint16_t originalSender, uint16_t sourceSequence, uint16_t id,
        uint16_t totalSize, uint8_t flags, uint16_t lastHop);
    void resetFragmentAssembly(FragmentAssembly &assembly);
    void sendFragmentStatus(
        uint16_t originalSender, uint16_t sourceSequence, uint16_t id,
        uint8_t fragmentCount, const uint8_t *received, uint16_t lastHop,
        uint16_t queryId);

    void addPacketToSendingQueue(DTPKPacketUnknown *packet,
                                 size_t size,
                                 uint16_t target,
                                 int32_t timeout,
                                 int32_t timeLeftToSend,
                                 bool lcmmAck = false,
                                 bool dtpkAck = false,
                                 PacketAckCallback callback = nullptr,
                                 bool priority = false);

    void sendCrystPacket();
    void queueCrystSnapshot();
    void sendHello();
    void sendCrystRequest(uint16_t neighbor);
    void sendSeqRequest(uint16_t destination, uint16_t requestedSequence,
                        bool flood);
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
    void parseFragmentPacket(const ReceivedPacket &packet);
    void parseFragmentStatusPacket(const ReceivedPacket &packet);
    void parseFragmentQueryPacket(const ReceivedPacket &packet);
    bool forwardRoutedPacket(const ReceivedPacket &packet);

    void receivingDeamon();
    void sendingDeamon();
    void timeoutDeamon();
    void controlDeamon();
};

#endif
