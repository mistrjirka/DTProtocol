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
        // For relayed application DATA, a failed next-hop LCMM transaction must
        // report a transient NACK to the immediately previous hop.
        uint16_t failurePreviousHop;
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
        // Single-frame E2E transactions retain one bounded source copy. A
        // retry reuses the same identity, so the destination can ACK again
        // without delivering the application payload twice.
        DTPKPacketUnknown *retryPacket = nullptr;
        size_t retrySize = 0;
        uint32_t nextRetryAt = 0;
        bool retryQueued = false;
        uint16_t retryCount = 0;
        uint16_t lastRouter = 0;
        uint16_t failedRouter = 0;
        bool routeRepairNeeded = false;
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
    // Sends ordinary application data while attaching one or more bits from
    // DTPK_APPLICATION_FLAGS_MASK. Transport-owned flag bits are ignored.
    uint16_t sendPacketWithFlags(
        uint16_t target, unsigned char *packet, size_t size,
        int32_t timeout, uint8_t applicationFlags,
        bool isAck = false,
        PacketAckCallback callback = nullptr);
    void loop();
    std::vector<NeighborRecord> getNeighbours();
    bool getRouteDetails(uint16_t destination, RoutingRecord &result) const;
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
    // One-shot repair waves need a bounded, reasonably early escape from a
    // stale directed candidate. Every fourth logical attempt is broadcast.
    static constexpr uint8_t SEQ_REQ_FLOOD_INTERVAL = 4;
    static constexpr uint16_t MAX_CRYST_CHUNKS = 16;
    // One replay window per active source for the validated <=255-node
    // network envelope. Unlike the old global ring, traffic from one source
    // cannot evict another source's still-retrying identity.
    static constexpr size_t REPLAY_SOURCE_CACHE_SIZE =
        DTPK_REPLAY_SOURCE_SLOTS;
    static constexpr size_t REPLAY_WINDOW_BITS = 64;
    static constexpr size_t RECENT_SEQ_REQ_CACHE_SIZE = 64;
    static constexpr size_t REVERSE_BREADCRUMB_CACHE_SIZE = 128;
    static constexpr uint32_t REVERSE_BREADCRUMB_EXPIRY_MS = 120000;
    static constexpr uint8_t MAX_REPAIR_BURST = 4;
    static constexpr uint8_t MAX_FRAGMENT_COUNT = 255;
    static constexpr uint32_t FRAGMENT_QUERY_INTERVAL_MS = 5000;
    static constexpr uint32_t FRAGMENT_ASSEMBLY_EXPIRY_MS = 120000;
    // DTPK gives a source fragment a 10 s request budget and relayed packets a
    // 5 s request budget; LCMM divides that into five retry attempts. These
    // conservative wall-clock budgets include retry jitter and airtime.
    static constexpr uint32_t FRAGMENT_SOURCE_HOP_BUDGET_MS = 20000;
    static constexpr uint32_t FRAGMENT_RELAY_HOP_BUDGET_MS = 10000;
    static constexpr uint32_t SINGLE_RETRY_BASE_MS = 30000;
    static constexpr uint32_t SINGLE_RETRY_PER_HOP_MS = 5000;
    static constexpr uint32_t SINGLE_RETRY_MAX_MS = 60000;
    static constexpr uint32_t SINGLE_LINK_FAILURE_BACKOFF_MS = 5000;
    static constexpr uint32_t SINGLE_RETRY_MIN_REMAINING_MS = 20000;
    // Per-attempt silence after a reliable hop. The complete application
    // timeout must never become one LCMM retry interval; five attempts plus
    // actual DATA/ACK airtime remain bounded independently.
    static constexpr uint32_t LCMM_LINK_ACK_WAIT_MS = 3000;
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

    struct ReplayWindow
    {
        uint64_t seenIds = 0;
        uint32_t lastUpdate = 0;
        uint16_t originalSender = 0; // zero marks an unused source slot
        uint16_t sourceSequence = 0;
        uint16_t newestId = 0;
    };

    static_assert(DTPK_REPLAY_SOURCE_SLOTS > 0,
                  "at least one replay source slot is required");
    static_assert(sizeof(ReplayWindow) <= 24,
                  "replay window footprint unexpectedly increased");

    struct SeqRequestIdentity
    {
        uint16_t originalSender = 0; // zero marks an unused ring entry
        uint16_t id = 0;
        uint16_t destination = 0;
        uint16_t requestedSequence = 0;
    };

    struct RelayFailureContext
    {
        uint16_t originalSender = 0;
        uint16_t sourceSequence = 0;
        uint16_t id = 0;
        uint16_t failedDestination = 0;
        uint16_t previousHop = 0;
    };

    struct ReverseBreadcrumb
    {
        uint16_t originalSender = 0; // zero marks an unused ring entry
        uint16_t sourceSequence = 0;
        uint16_t id = 0;
        uint16_t previousHop = 0;
        uint8_t distance = 0;
        uint32_t lastUpdate = 0;
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

    std::array<ReplayWindow, REPLAY_SOURCE_CACHE_SIZE> _replayWindows{};
    std::array<SeqRequestIdentity, RECENT_SEQ_REQ_CACHE_SIZE> _recentSeqRequests{};
    size_t _recentSeqRequestNext = 0;
    std::array<ReverseBreadcrumb, REVERSE_BREADCRUMB_CACHE_SIZE>
        _reverseBreadcrumbs{};
    size_t _reverseBreadcrumbNext = 0;

    uint32_t _currentTime;
    uint32_t _lastTick;
    uint16_t _packetCounter;
    uint16_t _originSequence;
    // A destination-generation repair may arrive while DATA using the current
    // generation is queued or in flight. Apply it only at an application-safe
    // boundary, then rebase unsent local DATA atomically.
    uint16_t _pendingOriginSequence;
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
    // LCMM id -> application identity for source single-frame transactions.
    std::unordered_map<uint16_t, PacketIdentity> _singleLcmmIds;
    // A no-E2E local packet has no DTPK waiting record while LCMM owns it.
    // Track that one serial in-flight slot so admission remains an exact bound.
    uint16_t _localNoE2ELcmmId = 0;
    std::unordered_map<uint16_t, RelayFailureContext> _relayedLcmmIds;
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
    static bool packetIdNewer(uint16_t a, uint16_t b);
    static bool versionNewer(uint32_t a, uint32_t b);
    static bool maySendDuringEndToEndWait(DTPKPacketType type);
    bool hasOutstandingEndToEndAck() const;
    size_t pendingLocalApplicationTransactions() const;
    bool hasUrgentRouteRepair() const;
    bool canAdvanceOriginSequence() const;
    void requestOriginSequenceAdvance(uint16_t requestedSequence);
    void applyPendingOriginSequence();
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
    DTPKPacketWaiting *findWaitingPacket(
        uint16_t id, uint16_t sourceSequence, uint16_t target);
    bool isActiveSingleRetry(const DTPKPacketRequest &request);
    uint32_t singleRetryDelayMs(uint16_t target) const;
    bool resolveRetryRoute(
        DTPKPacketWaiting &waiting,
        RoutingRecord &result,
        bool &fromReversePath) const;
    uint32_t singleLinkFailureBackoffMs(uint16_t retryCount) const;
    void scheduleSingleRetries();
    void removeQueuedSingleRetries(
        uint16_t id, uint16_t sourceSequence, uint16_t target);
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
    bool validateRoutedPacketShape(
        const ReceivedPacket &packet) const;
    bool applicationOriginIsStale(
        const DTPKPacketGeneric *packet) const;
    void learnDirectApplicationOrigin(
        const DTPKPacketGeneric *packet, uint16_t immediateSender);
    void rememberReverseBreadcrumb(
        const DTPKPacketGeneric *packet, uint16_t immediateSender);
    bool findReverseBreadcrumb(
        uint16_t originalSender, uint16_t sourceSequence, uint16_t id,
        uint16_t &previousHop) const;
    bool findRecentReverseRoute(
        uint16_t destination, uint16_t maxDistanceExclusive,
        RoutingRecord &result) const;
    bool resolveRoute(
        uint16_t destination, uint16_t maxDistanceExclusive,
        RoutingRecord &result, bool &fromReversePath) const;
    void expireReverseBreadcrumbs();
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
                                 bool priority = false,
                                 uint16_t failurePreviousHop = 0);

    void sendCrystPacket();
    void queueCrystSnapshot(
        uint16_t target = BROADCAST,
        bool reliable = false);
    void sendHello();
    void sendCrystRequest(uint16_t neighbor);
    void sendSeqRequest(uint16_t destination, uint16_t requestedSequence,
                        bool flood);
    void sendNackPacket(uint16_t target, uint16_t from, uint16_t id,
                        uint16_t sourceSequence,
                        uint16_t failedDestination,
                        bool finalReject = false,
                        uint16_t failedRouter = 0);
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
