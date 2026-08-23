#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

DTPK *DTPK::dtpk = nullptr;

bool DTPK::initialize(uint8_t KLimit, uint16_t originSequence, bool mobileHint)
{
    // KLimit belonged to the pre-v2 crystallization-session algorithm. Keep the
    // parameter for source compatibility, but v4 has no session window.
    (void)KLimit;
    MAC *mac = MAC::getInstance();
    if (!mac || !mac->isReady())
        return false;
    if (!dtpk)
        dtpk = new DTPK(originSequence, mobileHint);
    return dtpk != nullptr;
}

DTPK *DTPK::getInstance()
{
    return dtpk;
}

bool DTPK::sequenceNewer(uint16_t a, uint16_t b)
{
    if (a == b)
        return false;
    return static_cast<uint16_t>(a - b) < 0x8000u;
}

bool DTPK::packetIdNewer(uint16_t a, uint16_t b)
{
    if (a == b)
        return false;
    return static_cast<uint16_t>(a - b) < 0x8000u;
}

bool DTPK::versionNewer(uint32_t a, uint32_t b)
{
    if (a == b)
        return false;
    return static_cast<uint32_t>(a - b) < 0x80000000u;
}

bool DTPK::maySendDuringEndToEndWait(DTPKPacketType type)
{
    // A second independent application transaction is gated. Route-state
    // broadcasts and sequence repair must continue because the active DATA may
    // itself need a replacement route. Response traffic remains highest
    // priority, LCMM waits are independently bounded, and repair bursts are
    // capped by MAX_REPAIR_BURST.
    // CRYST_REQ is the exception: it is a reliable neighbor-sync request and
    // can consume five full silence intervals when that peer is unavailable.
    // Deferring one request is safe because the already-known active route and
    // SEQ_REQ/CRYST repair remain live; the queued request is coalesced and sent
    // immediately after the application transaction closes.
    return type == CRYST || type == HELLO || type == SEQ_REQ ||
           type == ACK || type == NACK_NOTFOUND ||
           type == DATA_FRAGMENT || type == FRAGMENT_STATUS ||
           type == FRAGMENT_QUERY;
}

bool DTPK::hasOutstandingEndToEndAck() const
{
    if (_multipartSend.active)
        return true;
    return std::any_of(
        _packetWaiting.begin(),
        _packetWaiting.end(),
        [](const DTPKPacketWaiting &waiting) { return !waiting.gotAck; });
}

size_t DTPK::pendingLocalApplicationTransactions() const
{
    // Every source-side E2E or multipart transaction owns one waiting record.
    // A queued same-identity DATA_SINGLE can be its message-level retry and must
    // not be counted twice. No-ACK local DATA has no waiting record, so count
    // its queued source packet directly.
    size_t count = _packetWaiting.size();
    if (_localNoE2ELcmmId != 0)
        ++count;
    MAC *mac = MAC::getInstance();
    const uint16_t localId = mac ? mac->getId() : 0;
    for (const DTPKPacketRequest &request : _packetRequests)
    {
        if (!request.packet || request.packet->type != DATA_SINGLE)
            continue;
        const DTPKPacketGeneric *generic =
            reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
        if (generic->originalSender != localId)
            continue;

        const bool representedByWaiting = std::any_of(
            _packetWaiting.begin(),
            _packetWaiting.end(),
            [generic](const DTPKPacketWaiting &waiting) {
                return waiting.id == generic->id &&
                       waiting.sourceSequence == generic->sourceSequence &&
                       waiting.target == generic->finalTarget;
            });
        if (!representedByWaiting)
            ++count;
    }
    return count;
}

bool DTPK::hasUrgentRouteRepair() const
{
    return std::any_of(
        _packetWaiting.begin(),
        _packetWaiting.end(),
        [](const DTPKPacketWaiting &waiting) {
            return !waiting.gotAck && waiting.routeRepairNeeded;
        });
}

bool DTPK::canAdvanceOriginSequence() const
{
    return !_multipartSend.active && _packetWaiting.empty() &&
           !LCMM::getInstance()->isSending();
}

void DTPK::requestOriginSequenceAdvance(uint16_t requestedSequence)
{
    const uint16_t normalized = requestedSequence == 0 ? 1 : requestedSequence;
    const uint16_t baseline =
        _pendingOriginSequence != 0 &&
                sequenceNewer(_pendingOriginSequence, _originSequence)
            ? _pendingOriginSequence
            : _originSequence;
    if (sequenceNewer(normalized, baseline))
        _pendingOriginSequence = normalized;
    applyPendingOriginSequence();
}

void DTPK::applyPendingOriginSequence()
{
    if (_pendingOriginSequence == 0 ||
        !sequenceNewer(_pendingOriginSequence, _originSequence))
    {
        _pendingOriginSequence = 0;
        return;
    }
    if (!canAdvanceOriginSequence())
        return;

    _originSequence = _pendingOriginSequence;
    _pendingOriginSequence = 0;
    ++_routeVersion;
    if (_routeVersion == 0)
        _routeVersion = 1;

    const uint16_t localId = MAC::getInstance()->getId();
    for (DTPKPacketRequest &request : _packetRequests)
    {
        if (!request.packet ||
            (request.packet->type != DATA_SINGLE &&
             request.packet->type != DATA_FRAGMENT &&
             request.packet->type != FRAGMENT_QUERY))
            continue;
        DTPKPacketGeneric *generic =
            reinterpret_cast<DTPKPacketGeneric *>(request.packet);
        if (generic->originalSender != localId)
            continue;
        generic->sourceSequence = _originSequence;
        rememberData(localId, _originSequence, generic->id);
    }

    sendHello();
    sendCrystPacket();
}

bool DTPK::hasKnownDirectNeighbor() const
{
    // _lastHeard contains only directly received neighbours and entries are
    // removed by the normal hard-liveness expiry. Reusing that ownership avoids
    // a second, arbitrary "recent" timeout for mobile cadence.
    return !_lastHeard.empty();
}

uint32_t DTPK::effectiveHelloPeriodMs() const
{
    uint32_t period = HELLO_PERIOD_MS;
    if (_mobileHint)
        period = hasKnownDirectNeighbor()
                     ? MOBILE_HELLO_PERIOD_MS
                     : MOBILE_DISCOVERY_HELLO_PERIOD_MS;

    const uint8_t duty = MAC::getInstance()->getFallbackDutyCyclePercent();
    if (duty > 0 && duty <= 1)
        period = std::max<uint32_t>(period, 60000u);
    return period;
}

DTPK::DTPK(uint16_t originSequence, bool mobileHint)
    : _crystDatabase(MAC::getInstance()->getId())
{
    _packetCounter = 0;
    _currentTime = millis();
    _lastTick = _currentTime;
    _mobileHint = mobileHint;

    const uint64_t seed = MathExtension.murmur64(
        (static_cast<uint64_t>(MAC::getInstance()->random()) << 32) |
        MAC::getInstance()->random());
    randomSeed(seed);

    if (originSequence == 0)
    {
        originSequence = static_cast<uint16_t>(MAC::getInstance()->random());
        if (originSequence == 0)
            originSequence = 1;
        Serial.println(
            "[DTPK] WARNING: non-persistent random boot incarnation; "
            "persist a monotonic sequence in production");
    }
    _originSequence = originSequence;
    _pendingOriginSequence = 0;
    _routeVersion = 1;
    const uint32_t helloPeriod = effectiveHelloPeriodMs();
    _helloRemaining = static_cast<int32_t>(
        random(100, static_cast<long>(helloPeriod + 1u)));
    _maintenanceRemaining = static_cast<int32_t>(MAINTENANCE_PERIOD_MS);

    LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);
    MAC::getInstance()->setMode(RECEIVING, true);

    sendCrystPacket();
}

void DTPK::setPacketReceivedCallback(PacketReceivedCallback callback)
{
    _recieveCallback = callback;
}

uint16_t DTPK::nextPacketId()
{
    ++_packetCounter;
    if (_packetCounter == 0)
        ++_packetCounter;
    return _packetCounter;
}

bool DTPK::hasSeenData(uint16_t originalSender, uint16_t sourceSequence,
                       uint16_t id) const
{
    if (originalSender == 0 || sourceSequence == 0 || id == 0)
        return true;

    for (const ReplayWindow &window : _replayWindows)
    {
        if (window.originalSender != originalSender)
            continue;

        if (window.sourceSequence == sourceSequence)
        {
            if (window.newestId == 0)
                return false;
            if (id == window.newestId)
                return (window.seenIds & 1ull) != 0;
            if (packetIdNewer(id, window.newestId))
                return false;
            if (packetIdNewer(window.newestId, id))
            {
                const uint16_t offset =
                    static_cast<uint16_t>(window.newestId - id);
                if (offset >= REPLAY_WINDOW_BITS)
                    return true; // too old to be a new in-window packet
                return (window.seenIds & (1ull << offset)) != 0;
            }
            // Exactly half the serial space is ambiguous. Reject rather than
            // risking a duplicate application delivery.
            return true;
        }

        if (sequenceNewer(window.sourceSequence, sourceSequence))
            return true; // delayed packet from an older boot incarnation
        if (sequenceNewer(sourceSequence, window.sourceSequence))
            return false; // newer incarnation starts a fresh replay window
        // Exactly half the serial space is ambiguous in RFC-1982 arithmetic.
        // Reject rather than risking a second application delivery.
        return true;
    }
    return false;
}

void DTPK::rememberData(uint16_t originalSender, uint16_t sourceSequence,
                        uint16_t id)
{
    if (originalSender == 0 || sourceSequence == 0 || id == 0)
        return;

    ReplayWindow *slot = nullptr;
    ReplayWindow *oldest = nullptr;
    uint32_t oldestAge = 0;
    for (ReplayWindow &window : _replayWindows)
    {
        if (window.originalSender == originalSender)
        {
            slot = &window;
            break;
        }
        if (window.originalSender == 0 && !slot)
            slot = &window;
        const uint32_t age =
            static_cast<uint32_t>(_currentTime - window.lastUpdate);
        if (!oldest || age > oldestAge)
        {
            oldest = &window;
            oldestAge = age;
        }
    }
    if (!slot)
        slot = oldest;
    if (!slot)
        return;

    if (slot->originalSender != originalSender ||
        slot->sourceSequence == 0 ||
        sequenceNewer(sourceSequence, slot->sourceSequence))
    {
        slot->originalSender = originalSender;
        slot->sourceSequence = sourceSequence;
        slot->newestId = id;
        slot->seenIds = 1ull;
        slot->lastUpdate = _currentTime;
        return;
    }
    if (slot->sourceSequence != sourceSequence)
        return; // stale or serially ambiguous incarnation

    if (slot->newestId == 0)
    {
        slot->newestId = id;
        slot->seenIds = 1ull;
    }
    else if (id == slot->newestId)
    {
        slot->seenIds |= 1ull;
    }
    else if (packetIdNewer(id, slot->newestId))
    {
        const uint16_t advance =
            static_cast<uint16_t>(id - slot->newestId);
        slot->seenIds =
            advance >= REPLAY_WINDOW_BITS
                ? 1ull
                : static_cast<uint64_t>((slot->seenIds << advance) | 1ull);
        slot->newestId = id;
    }
    else if (packetIdNewer(slot->newestId, id))
    {
        const uint16_t offset =
            static_cast<uint16_t>(slot->newestId - id);
        if (offset < REPLAY_WINDOW_BITS)
            slot->seenIds |= 1ull << offset;
    }
    slot->lastUpdate = _currentTime;
}

bool DTPK::hasSeenSeqRequest(uint16_t originalSender, uint16_t id,
                             uint16_t destination, uint16_t requestedSequence) const
{
    for (const SeqRequestIdentity &entry : _recentSeqRequests)
    {
        if (entry.originalSender == originalSender &&
            entry.id == id &&
            entry.destination == destination &&
            entry.requestedSequence == requestedSequence)
            return true;
    }
    return false;
}

void DTPK::rememberSeqRequest(uint16_t originalSender, uint16_t id,
                              uint16_t destination, uint16_t requestedSequence)
{
    if (hasSeenSeqRequest(originalSender, id, destination, requestedSequence))
        return;
    SeqRequestIdentity &entry = _recentSeqRequests[_recentSeqRequestNext];
    entry.originalSender = originalSender;
    entry.id = id;
    entry.destination = destination;
    entry.requestedSequence = requestedSequence;
    _recentSeqRequestNext =
        (_recentSeqRequestNext + 1) % RECENT_SEQ_REQ_CACHE_SIZE;
}

void DTPK::markRoutingChanged(const char *reason)
{
    (void)reason;
    ++_routeVersion;
    if (_routeVersion == 0)
        _routeVersion = 1;
    sendCrystPacket();
    processSequenceRequests();
}

void DTPK::noteHeard(uint16_t neighbor)
{
    if (neighbor == 0 || neighbor == MAC::getInstance()->getId())
        return;
    _lastHeard[neighbor] = _currentTime;
    _lastLivenessProbe.erase(neighbor);
}

void DTPK::expireNeighbours()
{
    std::vector<uint16_t> stale;
    stale.reserve(_lastHeard.size());

    for (const auto &entry : _lastHeard)
    {
        const uint16_t neighbor = entry.first;
        const uint32_t age =
            static_cast<uint32_t>(_currentTime - entry.second);
        // In a lossy half-duplex network, even several unanswered reliable
        // probes are not proof that the neighbour disappeared.  Probes repair
        // state and can prove liveness when they succeed; only sustained lack
        // of any valid packet is allowed to withdraw topology.
        if (age >= _neighborHardExpiryMs)
        {
            stale.push_back(neighbor);
            continue;
        }

        if (age < NEIGHBOR_SUSPECT_MS)
            continue;

        const auto last = _lastLivenessProbe.find(neighbor);
        if (last == _lastLivenessProbe.end() ||
            static_cast<uint32_t>(_currentTime - last->second) >=
                LIVENESS_PROBE_COOLDOWN_MS)
        {
            // CRYST_REQ is already a tiny reliable direct-neighbor packet. Its
            // LCMM ACK proves liveness, while its CRYST response repairs any
            // indirect state that may have gone stale during the quiet period.
            sendCrystRequest(neighbor);
            _lastLivenessProbe[neighbor] = _currentTime;
        }
    }

    for (uint16_t neighbor : stale)
    {
        _lastHeard.erase(neighbor);
        _lastLivenessProbe.erase(neighbor);
        _neighborState.erase(neighbor);
        _crystAssemblies.erase(neighbor);

        // Remove stale outstanding LCMM-id mappings to this neighbor.
        for (auto it = _directAckNeighborByLcmmId.begin();
             it != _directAckNeighborByLcmmId.end();)
        {
            if (it->second == neighbor)
                it = _directAckNeighborByLcmmId.erase(it);
            else
                ++it;
        }

        if (_crystDatabase.removeNeighbor(neighbor))
            markRoutingChanged("neighbor expired");
    }
}

void DTPK::expireAssemblies()
{
    std::vector<uint16_t> stale;
    for (const auto &entry : _crystAssemblies)
    {
        if (static_cast<uint32_t>(_currentTime - entry.second.lastUpdate) >=
            CRYST_ASSEMBLY_EXPIRY_MS)
            stale.push_back(entry.first);
    }
    for (uint16_t neighbor : stale)
        _crystAssemblies.erase(neighbor);
}

void DTPK::processSequenceRequests()
{
    for (const CrystSequenceRequest &request : _crystDatabase.takeSequenceRequests())
    {
        auto existing = _pendingSeqRequests.find(request.destination);
        if (existing == _pendingSeqRequests.end())
        {
            _pendingSeqRequests.emplace(
                request.destination,
                PendingSeqRequest{request.requestedSequence, 0, 0});
            continue;
        }
        if (sequenceNewer(request.requestedSequence,
                          existing->second.requestedSequence))
        {
            existing->second.requestedSequence = request.requestedSequence;
            existing->second.lastSent = 0;
            existing->second.attempts = 0;
        }
    }
}

void DTPK::retrySequenceRequests()
{
    for (auto it = _pendingSeqRequests.begin(); it != _pendingSeqRequests.end();)
    {
        const uint16_t destination = it->first;
        PendingSeqRequest &pending = it->second;

        RoutingRecord *route = _crystDatabase.getRouting(destination);
        if (route != nullptr)
        {
            // A sequence request is a liveness repair for the state in which no
            // feasible route can be selected. Once any feasible route exists,
            // continuing to chase the originally requested generation is both
            // unnecessary and harmful: an older lower-metric route may quite
            // correctly win route selection while a newer candidate remains
            // available. Repeatedly advancing the destination in that state
            // creates a self-sustaining generation/CRYST storm.
            const uint16_t localId = MAC::getInstance()->getId();
            auto queuedEnd = std::remove_if(
                _packetRequests.begin(),
                _packetRequests.end(),
                [destination, localId](DTPKPacketRequest &request) {
                    if (!request.packet || request.packet->type != SEQ_REQ)
                        return false;
                    DTPKPacketSeqRequest *packet =
                        reinterpret_cast<DTPKPacketSeqRequest *>(request.packet);
                    if (packet->destination != destination ||
                        packet->originalSender != localId)
                        return false;
                    free(request.packet);
                    request.packet = nullptr;
                    return true;
                });
            _packetRequests.erase(queuedEnd, _packetRequests.end());
            it = _pendingSeqRequests.erase(it);
            continue;
        }

        // If all contributions disappeared, the destination is no longer a
        // feasibility-blocked known route. Stop repairing it. Otherwise retry
        // until a sufficiently fresh generation is actually learned: a finite
        // fire-and-forget burst is not a liveness guarantee on lossy RF.
        if (route == nullptr &&
            !_crystDatabase.hasKnownDestination(destination))
        {
            it = _pendingSeqRequests.erase(it);
            continue;
        }

        uint32_t retryDelay = 0;
        if (pending.attempts > 0)
        {
            const uint8_t shift = std::min<uint8_t>(
                static_cast<uint8_t>(pending.attempts - 1u), 4u);
            const uint32_t scaled = SEQ_REQ_RETRY_MIN_MS << shift;
            retryDelay = std::min<uint32_t>(
                scaled, SEQ_REQ_RETRY_MAX_MS);
        }
        const bool due =
            pending.attempts == 0 ||
            static_cast<uint32_t>(_currentTime - pending.lastSent) >=
                retryDelay;
        if (due)
        {
            // Candidate-guided one-shot unicast is the normal repair path.
            // Logical retries persist here with backoff; a periodic flood is an
            // explicit escape from stale candidates or missing contributions.
            const bool flood =
                ((static_cast<uint32_t>(pending.attempts) + 1u) %
                 SEQ_REQ_FLOOD_INTERVAL) == 0u;
            sendSeqRequest(
                destination,
                pending.requestedSequence,
                flood);
            pending.lastSent = _currentTime;
            if (pending.attempts < 0xffffu)
                ++pending.attempts;
            else
                pending.attempts = 0;
        }
        ++it;
    }
}

void DTPK::addPacketToSendingQueue(
    DTPKPacketUnknown *packet,
    size_t size,
    uint16_t target,
    int32_t timeout,
    int32_t timeLeftToSend,
    bool lcmmAck,
    bool dtpkAck,
    PacketAckCallback callback,
    bool priority,
    uint16_t failurePreviousHop)
{
    if (!packet)
        return;

    if (size > DATASIZE_LCMM)
    {
        if (callback)
            callback(0, 0);
        free(packet);
        return;
    }

    uint8_t queueClass = TX_NORMAL;
    if (packet->type == ACK || packet->type == NACK_NOTFOUND ||
        packet->type == FRAGMENT_STATUS)
        queueClass = TX_RESPONSE;
    else if (priority || packet->type == CRYST_REQ ||
             packet->type == SEQ_REQ || packet->type == FRAGMENT_QUERY)
        queueClass = TX_REPAIR;

    _packetRequests.push_back(DTPKPacketRequest{
        packet,
        size,
        target,
        timeout,
        timeLeftToSend,
        lcmmAck,
        dtpkAck,
        callback,
        queueClass,
        failurePreviousHop});
}

void DTPK::sendingDeamon()
{
    if (_packetRequests.empty())
        return;

    const uint32_t elapsed = _currentTime - _lastTick;
    for (DTPKPacketRequest &request : _packetRequests)
    {
        if (request.timeLeftToSend <= 0)
            continue;
        request.timeLeftToSend =
            elapsed >= static_cast<uint32_t>(request.timeLeftToSend)
                ? 0
                : request.timeLeftToSend - static_cast<int32_t>(elapsed);
    }

    if (LCMM::getInstance()->isSending())
        return;

    const size_t none = _packetRequests.size();
    size_t response = none;
    size_t repair = none;
    size_t normal = none;
    for (size_t i = 0; i < _packetRequests.size(); ++i)
    {
        const DTPKPacketRequest &request = _packetRequests[i];
        if (request.timeLeftToSend > 0)
            continue;
        const DTPKPacketType type = request.packet->type;
        bool relayedApplication = false;
        bool immediateApplicationResponse = false;
        if (type == DATA_SINGLE)
        {
            const DTPKPacketGeneric *generic =
                reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
            relayedApplication =
                generic->originalSender != MAC::getInstance()->getId();
            immediateApplicationResponse =
                (generic->flags & DTPK_FLAG_DEBUG_ECHO) != 0;
        }
        const bool urgentCrystRequest =
            type == CRYST_REQ && hasUrgentRouteRepair();
        if (hasOutstandingEndToEndAck() &&
            !maySendDuringEndToEndWait(type) &&
            !urgentCrystRequest &&
            !isActiveSingleRetry(request) &&
            !relayedApplication &&
            !immediateApplicationResponse)
            continue;
        if (request.queueClass == TX_RESPONSE && response == none)
            response = i;
        else if (request.queueClass == TX_REPAIR && repair == none)
            repair = i;
        else if (request.queueClass == TX_NORMAL && normal == none)
            normal = i;
    }

    size_t selected = none;
    if (response != none)
        selected = response;
    else if (repair != none && (normal == none || _repairBurst < MAX_REPAIR_BURST))
        selected = repair;
    else if (normal != none)
        selected = normal;
    else if (repair != none)
        selected = repair;
    if (selected == none)
        return;

    DTPKPacketRequest &request = _packetRequests[selected];
    if (request.packet->type == DATA_SINGLE ||
        request.packet->type == DATA_FRAGMENT ||
        request.packet->type == FRAGMENT_QUERY)
    {
        // A packet can wait behind another end-to-end transaction or radio
        // policy for long enough that its originally selected next hop becomes
        // stale. Resolve the final destination again immediately before LCMM
        // takes ownership; ACK/NACK/status packets deliberately retain their
        // reverse breadcrumb instead.
        DTPKPacketGeneric *generic =
            reinterpret_cast<DTPKPacketGeneric *>(request.packet);
        RoutingRecord fresh{};
        bool reversePath = false;
        bool haveFresh = false;
        DTPKPacketWaiting *waiting = nullptr;
        if (generic->originalSender == MAC::getInstance()->getId())
            waiting = findWaitingPacket(
                generic->id,
                generic->sourceSequence,
                generic->finalTarget);
        if (waiting)
            haveFresh = resolveRetryRoute(*waiting, fresh, reversePath);
        else
        {
            const uint16_t maxDistance =
                generic->hopLimit == DTPK_DEFAULT_HOP_LIMIT
                    ? 256u
                    : static_cast<uint16_t>(generic->hopLimit) + 1u;
            haveFresh = resolveRoute(
                generic->finalTarget,
                maxDistance,
                fresh,
                reversePath);
        }
        if (haveFresh)
        {
            request.target = fresh.router;
            // Re-routing changes only the next hop. hopLimit records hops
            // already travelled from the packet's original source.
        }
    }
    const uint32_t lcmmTimeout = request.lcmmAck
        ? static_cast<uint32_t>(std::min<int32_t>(
              LCMM_LINK_ACK_WAIT_MS,
              std::max<int32_t>(1, request.timeout)))
        : static_cast<uint32_t>(std::max<int32_t>(1, request.timeout));
    const uint16_t lcmmId = LCMM::getInstance()->sendPacketSingle(
        request.lcmmAck,
        request.target,
        reinterpret_cast<unsigned char *>(request.packet),
        static_cast<uint8_t>(request.size),
        DTPK::receiveAck,
        lcmmTimeout,
        5);

    if (lcmmId == 0)
    {
        const uint8_t sendResult = LCMM::getInstance()->getLastSendResult();
        const bool fatal =
            sendResult == MAC_SEND_ALLOC_FAILED ||
            sendResult == MAC_SEND_TOO_LARGE ||
            sendResult == MAC_SEND_RADIO_ERROR;

        if (!fatal)
        {
            const uint32_t wait = MAC::getInstance()->getTransmitWaitMs();
            const uint32_t bounded = std::min<uint32_t>(
                wait > 0 ? wait : 1u,
                0x7fffffffu);
            request.timeLeftToSend = static_cast<int32_t>(bounded);
            return;
        }

        RelayFailureContext relayFailure{};
        bool reportRelayFailure = false;
        if (request.packet->type == DATA_SINGLE ||
            request.packet->type == DATA_FRAGMENT)
        {
            DTPKPacketGeneric *generic =
                reinterpret_cast<DTPKPacketGeneric *>(request.packet);
            if (request.packet->type == DATA_SINGLE && !request.dtpkAck)
            {
                DTPKPacketWaiting *waiting = findWaitingPacket(
                    generic->id,
                    generic->sourceSequence,
                    generic->finalTarget);
                if (waiting)
                {
                    waiting->retryQueued = false;
                    waiting->routeRepairNeeded = true;
                    waiting->failedRouter = request.target;
                    waiting->nextRetryAt =
                        _currentTime +
                        singleLinkFailureBackoffMs(waiting->retryCount);
                }
            }
            if (request.failurePreviousHop != 0)
            {
                relayFailure.originalSender = generic->originalSender;
                relayFailure.sourceSequence = generic->sourceSequence;
                relayFailure.id = generic->id;
                relayFailure.failedDestination = generic->finalTarget;
                relayFailure.previousHop = request.failurePreviousHop;
                reportRelayFailure = true;
            }
        }

        // Drop all references into the vector before enqueueing a NACK or
        // invoking application code; either action may grow _packetRequests.
        PacketAckCallback failureCallback = request.callback;
        DTPKPacketUnknown *failedPacket = request.packet;
        request.packet = nullptr;
        _packetRequests.erase(
            _packetRequests.begin() + static_cast<long>(selected));
        free(failedPacket);

        if (reportRelayFailure)
        {
            sendNackPacket(
                relayFailure.originalSender,
                relayFailure.previousHop,
                relayFailure.id,
                relayFailure.sourceSequence,
                relayFailure.failedDestination,
                false);
        }
        if (failureCallback)
            failureCallback(0, 0);
        return;
    }

    if (request.queueClass == TX_REPAIR)
    {
        if (_repairBurst < 0xffu)
            ++_repairBurst;
    }
    else if (request.queueClass == TX_NORMAL)
    {
        _repairBurst = 0;
    }

    if (request.lcmmAck && request.target != BROADCAST)
        _directAckNeighborByLcmmId[lcmmId] = request.target;
    if (_multipartSend.active &&
        (request.packet->type == DATA_FRAGMENT ||
         request.packet->type == FRAGMENT_QUERY))
    {
        const DTPKPacketGeneric *multipart =
            reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
        if (request.packet->id == _multipartSend.id &&
            multipart->sourceSequence == _multipartSend.sourceSequence &&
            multipart->originalSender == MAC::getInstance()->getId() &&
            multipart->finalTarget == _multipartSend.target)
        {
            uint8_t marker = 0; // zero identifies a status query
            if (request.packet->type == DATA_FRAGMENT)
            {
                const DTPKPacketFragment *fragment =
                    reinterpret_cast<const DTPKPacketFragment *>(
                        request.packet);
                marker = static_cast<uint8_t>(fragment->fragmentIndex + 1u);
            }
            _multipartLcmmIds[lcmmId] = marker;
        }
    }

    if (request.failurePreviousHop != 0 &&
        (request.packet->type == DATA_SINGLE ||
         request.packet->type == DATA_FRAGMENT))
    {
        const DTPKPacketGeneric *generic =
            reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
        RelayFailureContext context{};
        context.originalSender = generic->originalSender;
        context.sourceSequence = generic->sourceSequence;
        context.id = generic->id;
        context.failedDestination = generic->finalTarget;
        context.previousHop = request.failurePreviousHop;
        _relayedLcmmIds[lcmmId] = context;
    }

    const DTPKPacketGeneric *sentGeneric =
        request.packet->type == DATA_SINGLE
            ? reinterpret_cast<const DTPKPacketGeneric *>(request.packet)
            : nullptr;
    if (request.dtpkAck)
    {
        const DTPKPacketGeneric *generic =
            reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
        DTPKPacketWaiting waiting{};
        waiting.id = request.packet->id;
        waiting.sourceSequence = generic->sourceSequence;
        waiting.target = generic->finalTarget;
        waiting.timeout =
            request.timeout > 0 ? static_cast<uint32_t>(request.timeout) : 1u;
        waiting.timeLeft = request.timeout > 0 ? request.timeout : 1;
        waiting.gotAck = false;
        waiting.success = false;
        waiting.callback = request.callback;
        waiting.lastRouter = request.target;
        waiting.retryQueued = request.packet->type == DATA_SINGLE;
        if (request.packet->type == DATA_SINGLE)
        {
            waiting.retryPacket = static_cast<DTPKPacketUnknown *>(
                malloc(request.size));
            if (waiting.retryPacket)
            {
                memcpy(waiting.retryPacket, request.packet, request.size);
                waiting.retrySize = request.size;
                waiting.nextRetryAt =
                    _currentTime + singleRetryDelayMs(waiting.target);
            }
        }
        _packetWaiting.push_back(waiting);
        if (request.packet->type == DATA_SINGLE)
        {
            PacketIdentity identity{};
            identity.originalSender = generic->originalSender;
            identity.sourceSequence = generic->sourceSequence;
            identity.id = generic->id;
            _singleLcmmIds[lcmmId] = identity;
        }
    }
    else if (sentGeneric && sentGeneric->originalSender == MAC::getInstance()->getId())
    {
        DTPKPacketWaiting *waiting = findWaitingPacket(
            sentGeneric->id,
            sentGeneric->sourceSequence,
            sentGeneric->finalTarget);
        if (waiting)
        {
            // scheduleSingleRetries marked this copy queued. Keep the marker
            // until LCMM reports success/failure so a route change cannot queue
            // another same-identity retry while this one is still in flight.
            waiting->lastRouter = request.target;
            waiting->nextRetryAt =
                _currentTime + singleRetryDelayMs(waiting->target);
            PacketIdentity identity{};
            identity.originalSender = sentGeneric->originalSender;
            identity.sourceSequence = sentGeneric->sourceSequence;
            identity.id = sentGeneric->id;
            _singleLcmmIds[lcmmId] = identity;
        }
        else
        {
            _localNoE2ELcmmId = lcmmId;
        }
    }

    free(request.packet);
    _packetRequests.erase(
        _packetRequests.begin() + static_cast<long>(selected));
}
