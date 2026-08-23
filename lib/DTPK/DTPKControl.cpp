#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

DTPK::DTPKPacketWaiting *DTPK::findWaitingPacket(
    uint16_t id,
    uint16_t sourceSequence,
    uint16_t target)
{
    for (DTPKPacketWaiting &waiting : _packetWaiting)
    {
        if (waiting.id == id &&
            waiting.sourceSequence == sourceSequence &&
            waiting.target == target)
            return &waiting;
    }
    return nullptr;
}

bool DTPK::isActiveSingleRetry(const DTPKPacketRequest &request)
{
    if (!request.packet || request.packet->type != DATA_SINGLE ||
        request.dtpkAck)
        return false;
    const DTPKPacketGeneric *generic =
        reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
    if (generic->originalSender != MAC::getInstance()->getId())
        return false;
    DTPKPacketWaiting *waiting = findWaitingPacket(
        generic->id, generic->sourceSequence, generic->finalTarget);
    return waiting && !waiting->gotAck;
}

uint32_t DTPK::singleRetryDelayMs(uint16_t target) const
{
    RoutingRecord route{};
    bool reversePath = false;
    uint32_t hops = 1;
    if (resolveRoute(target, 256u, route, reversePath))
        hops = std::max<uint32_t>(1u, route.distance);
    const uint64_t delay =
        static_cast<uint64_t>(SINGLE_RETRY_BASE_MS) +
        static_cast<uint64_t>(hops) * SINGLE_RETRY_PER_HOP_MS;
    return static_cast<uint32_t>(
        std::min<uint64_t>(delay, SINGLE_RETRY_MAX_MS));
}

bool DTPK::resolveRetryRoute(
    DTPKPacketWaiting &waiting,
    RoutingRecord &result,
    bool &fromReversePath) const
{
    const bool haveSelected =
        resolveRoute(waiting.target, 256u, result, fromReversePath);
    if (haveSelected &&
        (waiting.failedRouter == 0 || result.router != waiting.failedRouter))
        return true;

    if (waiting.failedRouter != 0)
    {
        RoutingRecord alternate{};
        if (_crystDatabase.getFeasibleAlternateRoute(
                waiting.target, waiting.failedRouter, alternate))
        {
            result = alternate;
            fromReversePath = false;
            return true;
        }

        RoutingRecord reverse{};
        if (findRecentReverseRoute(waiting.target, 256u, reverse) &&
            reverse.router != waiting.failedRouter)
        {
            result = reverse;
            fromReversePath = true;
            return true;
        }

        // If no alternate exists, retry the selected branch only after the
        // failure backoff. This preserves recovery from a transient loss on the
        // sole route without immediately hammering the same failed hop.
        if (haveSelected &&
            static_cast<int32_t>(_currentTime - waiting.nextRetryAt) >= 0)
            return true;
    }
    return false;
}

uint32_t DTPK::singleLinkFailureBackoffMs(uint16_t retryCount) const
{
    const uint32_t multiplier = std::min<uint32_t>(
        static_cast<uint32_t>(retryCount) + 1u, 6u);
    return SINGLE_LINK_FAILURE_BACKOFF_MS * multiplier;
}

void DTPK::removeQueuedSingleRetries(
    uint16_t id,
    uint16_t sourceSequence,
    uint16_t target)
{
    auto end = std::remove_if(
        _packetRequests.begin(),
        _packetRequests.end(),
        [id, sourceSequence, target](DTPKPacketRequest &request) {
            if (!request.packet || request.packet->type != DATA_SINGLE ||
                request.dtpkAck)
                return false;
            const DTPKPacketGeneric *generic =
                reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
            if (generic->originalSender != MAC::getInstance()->getId() ||
                generic->id != id ||
                generic->sourceSequence != sourceSequence ||
                generic->finalTarget != target)
                return false;
            free(request.packet);
            request.packet = nullptr;
            return true;
        });
    _packetRequests.erase(end, _packetRequests.end());
}

void DTPK::scheduleSingleRetries()
{
    for (DTPKPacketWaiting &waiting : _packetWaiting)
    {
        if (waiting.gotAck ||
            waiting.timeLeft <= static_cast<int32_t>(
                SINGLE_RETRY_MIN_REMAINING_MS) ||
            !waiting.retryPacket || waiting.retrySize == 0 ||
            waiting.retryQueued)
            continue;

        RoutingRecord route{};
        bool reversePath = false;
        if (!resolveRetryRoute(waiting, route, reversePath))
        {
            waiting.routeRepairNeeded = true;
            waiting.nextRetryAt =
                _currentTime + singleLinkFailureBackoffMs(waiting.retryCount);
            continue;
        }

        // A newly selected next hop is immediate evidence that crystallization
        // found a repair path; do not wait for the ordinary E2E retry timer.
        const bool routeChanged =
            waiting.lastRouter != 0 && route.router != waiting.lastRouter;
        if (!routeChanged &&
            static_cast<int32_t>(_currentTime - waiting.nextRetryAt) < 0)
            continue;

        DTPKPacketUnknown *copy =
            static_cast<DTPKPacketUnknown *>(malloc(waiting.retrySize));
        if (!copy)
        {
            waiting.nextRetryAt =
                _currentTime + singleLinkFailureBackoffMs(waiting.retryCount);
            continue;
        }
        memcpy(copy, waiting.retryPacket, waiting.retrySize);
        DTPKPacketGeneric *generic =
            reinterpret_cast<DTPKPacketGeneric *>(copy);
        // A retry is still the same source packet; changing next hop must
        // not redefine how many hops it has already travelled.
        generic->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

        addPacketToSendingQueue(
            copy,
            waiting.retrySize,
            route.router,
            std::max<int32_t>(1, waiting.timeLeft),
            0,
            true,
            false,
            nullptr,
            false);
        waiting.retryQueued = true;
        waiting.lastRouter = route.router;
        if (routeChanged)
            waiting.routeRepairNeeded = false;
        waiting.nextRetryAt =
            _currentTime + singleRetryDelayMs(waiting.target);
        if (waiting.retryCount < UINT16_MAX)
            ++waiting.retryCount;
    }
}

void DTPK::timeoutDeamon()
{
    if (_packetWaiting.empty())
        return;

    struct Completion
    {
        PacketAckCallback callback;
        uint8_t result;
        uint16_t ping;
    };

    const uint32_t elapsed = _currentTime - _lastTick;
    std::vector<size_t> remove;
    std::vector<Completion> completions;
    remove.reserve(_packetWaiting.size());
    completions.reserve(_packetWaiting.size());

    for (size_t i = 0; i < _packetWaiting.size(); ++i)
    {
        DTPKPacketWaiting &waiting = _packetWaiting[i];
        const bool completed = waiting.gotAck;
        const bool expired = !completed && waiting.timeLeft <= 0;
        if (completed || expired)
        {
            const bool multipart =
                _multipartSend.active && waiting.id == _multipartSend.id &&
                waiting.sourceSequence == _multipartSend.sourceSequence;
            const bool success = completed && waiting.success;
            const uint16_t ping = success
                ? static_cast<uint16_t>(std::min<uint32_t>(
                      waiting.timeout - static_cast<uint32_t>(
                          std::max<int32_t>(waiting.timeLeft, 0)),
                      UINT16_MAX))
                : 0;
            if (waiting.callback)
                completions.push_back(
                    Completion{
                        waiting.callback,
                        static_cast<uint8_t>(success ? 1u : 0u),
                        ping});
            removeQueuedSingleRetries(
                waiting.id, waiting.sourceSequence, waiting.target);
            for (auto lcmm = _singleLcmmIds.begin();
                 lcmm != _singleLcmmIds.end();)
            {
                if (lcmm->second.id == waiting.id &&
                    lcmm->second.sourceSequence == waiting.sourceSequence)
                    lcmm = _singleLcmmIds.erase(lcmm);
                else
                    ++lcmm;
            }
            if (waiting.retryPacket)
            {
                free(waiting.retryPacket);
                waiting.retryPacket = nullptr;
                waiting.retrySize = 0;
            }
            if (multipart)
                clearMultipartSend();
            remove.push_back(i);
            continue;
        }

        waiting.timeLeft =
            elapsed >= static_cast<uint32_t>(waiting.timeLeft)
                ? 0
                : waiting.timeLeft - static_cast<int32_t>(elapsed);
    }

    for (auto it = remove.rbegin(); it != remove.rend(); ++it)
        _packetWaiting.erase(_packetWaiting.begin() + static_cast<long>(*it));

    // A sequence repair requested while DATA was active becomes authoritative
    // at this exact transaction boundary. Apply it before callbacks can enqueue
    // another application packet and before the scheduler transmits an older
    // queued packet.
    applyPendingOriginSequence();

    // Callbacks are deliberately invoked after all vector erases and multipart
    // cleanup. Applications are allowed to call sendPacket() reentrantly.
    for (Completion &completion : completions)
        completion.callback(completion.result, completion.ping);
}

void DTPK::sendCrystPacket()
{
    if (_crystRemaining >= 0)
        return;

    _crystRemaining = static_cast<int32_t>(random(
            static_cast<long>(CRYST_JITTER_MIN_MS),
            static_cast<long>(CRYST_JITTER_MAX_MS + 1u)));
}

void DTPK::queueCrystSnapshot(uint16_t target, bool reliable)
{
    // A newly generated state version supersedes all unsent chunks from older
    // CRYST snapshots. Preserve queued HELLO: liveness must not depend on a
    // churn-heavy multi-chunk snapshot ever reaching the head of the queue.
    auto end = std::remove_if(
        _packetRequests.begin(),
        _packetRequests.end(),
        [target](DTPKPacketRequest &request) {
            if (!request.packet || request.packet->type != CRYST ||
                request.target != target)
                return false;
            free(request.packet);
            request.packet = nullptr;
            return true;
        });
    _packetRequests.erase(end, _packetRequests.end());

    _crystDatabase.noteAdvertisedRoutes();
    const std::vector<NeighborRecordV2> routes =
        _crystDatabase.getListOfRoutesV2();

    if (DATASIZE_LCMM <= sizeof(DTPKPacketCryst))
        return;

    const size_t maxRecords =
        (DATASIZE_LCMM - sizeof(DTPKPacketCryst)) / sizeof(NeighborRecordV2);
    if (maxRecords == 0)
        return;

    size_t chunkCountSize =
        routes.empty() ? 1 : (routes.size() + maxRecords - 1) / maxRecords;
    if (chunkCountSize > MAX_CRYST_CHUNKS)
        return;

    const uint16_t chunkCount = static_cast<uint16_t>(chunkCountSize);

    for (uint16_t chunk = 0; chunk < chunkCount; ++chunk)
    {
        const size_t begin = static_cast<size_t>(chunk) * maxRecords;
        const size_t end = std::min(routes.size(), begin + maxRecords);
        const size_t count = end - begin;
        const size_t bytes =
            sizeof(DTPKPacketCryst) + count * sizeof(NeighborRecordV2);

        DTPKPacketCryst *packet =
            static_cast<DTPKPacketCryst *>(malloc(bytes));
        if (!packet)
            continue;

        packet->type = CRYST;
        packet->originSequence = _originSequence;
        packet->routeVersion = _routeVersion;
        packet->chunkIndex = chunk;
        packet->chunkCount = chunkCount;
        for (size_t i = 0; i < count; ++i)
            packet->neighbors[i] = routes[begin + i];

        addPacketToSendingQueue(
            reinterpret_cast<DTPKPacketUnknown *>(packet),
            bytes,
            target,
            5000,
            0,
            reliable,
            false,
            nullptr,
            reliable);
    }
}

void DTPK::sendHello()
{
    // Queue one replaceable HELLO even while the radio is busy or duty-limited.
    // The normal scheduler defers it; dropping the timer event can phase-lock a
    // busy node into silence for many beacon periods.
    for (const DTPKPacketRequest &request : _packetRequests)
    {
        if (request.packet && request.packet->type == HELLO)
            return;
    }

    DTPKPacketHello *packet =
        static_cast<DTPKPacketHello *>(malloc(sizeof(DTPKPacketHello)));
    if (!packet)
        return;

    packet->type = HELLO;
    packet->originSequence = _originSequence;
    packet->routeVersion = _routeVersion;

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketHello),
        BROADCAST,
        2000,
        0,
        false,
        false);
}

void DTPK::sendCrystRequest(uint16_t neighbor)
{
    // HELLO repair and liveness probing share the same reliable request. Keep
    // at most one unsent request per neighbor so a congested queue cannot turn
    // suspicion into a CRYST_REQ burst.
    for (const DTPKPacketRequest &queued : _packetRequests)
    {
        if (queued.packet && queued.packet->type == CRYST_REQ &&
            queued.target == neighbor)
            return;
    }

    DTPKPacketCrystRequest *packet =
        static_cast<DTPKPacketCrystRequest *>(
            malloc(sizeof(DTPKPacketCrystRequest)));
    if (!packet)
        return;

    packet->type = CRYST_REQ;
    packet->originSequence = _originSequence;
    packet->routeVersion = _routeVersion;

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketCrystRequest),
        neighbor,
        3000,
        static_cast<int32_t>(random(25, 501)),
        true,
        false,
        nullptr,
        true);
}

void DTPK::sendSeqRequest(uint16_t destination,
                          uint16_t requestedSequence,
                          bool flood)
{
    const uint16_t normalizedSequence =
        requestedSequence == 0 ? 1 : requestedSequence;
    uint16_t nextHop = BROADCAST;
    const bool directed =
        !flood &&
        _crystDatabase.getRepairNextHop(
            destination,
            BROADCAST,
            nextHop);

    for (DTPKPacketRequest &queued : _packetRequests)
    {
        if (!queued.packet || queued.packet->type != SEQ_REQ)
            continue;
        DTPKPacketSeqRequest *existing =
            reinterpret_cast<DTPKPacketSeqRequest *>(queued.packet);
        if (existing->destination != destination)
            continue;
        if (sequenceNewer(existing->requestedSequence, normalizedSequence))
            return;

        if (sequenceNewer(normalizedSequence, existing->requestedSequence))
        {
            existing->id = nextPacketId();
            existing->requestedSequence = normalizedSequence;
            rememberSeqRequest(
                existing->originalSender,
                existing->id,
                existing->destination,
                existing->requestedSequence);
        }

        // A later flood escape or changed best candidate must update the
        // queued request rather than being hidden by coalescing.
        existing->flags = directed ? 0 : DTPK_SEQ_REQ_FLOOD;
        existing->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
        queued.target = directed ? nextHop : BROADCAST;
        // Coalescing must preserve the one-shot link policy used for newly
        // allocated SEQ_REQ packets.
        queued.lcmmAck = false;
        queued.timeLeftToSend = 0;
        return;
    }

    DTPKPacketSeqRequest *packet =
        static_cast<DTPKPacketSeqRequest *>(malloc(sizeof(DTPKPacketSeqRequest)));
    if (!packet)
        return;

    packet->type = SEQ_REQ;
    packet->id = nextPacketId();
    packet->originalSender = MAC::getInstance()->getId();
    packet->destination = destination;
    packet->requestedSequence = normalizedSequence;
    packet->flags = directed ? 0 : DTPK_SEQ_REQ_FLOOD;
    packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

    rememberSeqRequest(
        packet->originalSender,
        packet->id,
        packet->destination,
        packet->requestedSequence);

    // SEQ_REQ already has persistent logical retry with exponential backoff
    // and a periodic flood escape. Five LCMM attempts at every directed hop
    // multiply one repair wave and can delay the CRYST snapshots needed to
    // satisfy it. Send this wave once; retrySequenceRequests() owns recovery.
    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketSeqRequest),
        directed ? nextHop : BROADCAST,
        3000,
        0,
        false,
        false,
        nullptr,
        true);
}

void DTPK::sendNackPacket(uint16_t target, uint16_t from, uint16_t id,
                          uint16_t sourceSequence,
                          uint16_t failedDestination,
                          bool finalReject,
                          uint16_t failedRouter)
{
    DTPKPacketNack *packet =
        static_cast<DTPKPacketNack *>(malloc(sizeof(DTPKPacketNack)));
    if (!packet)
        return;

    packet->type = NACK_NOTFOUND;
    packet->id = id;
    packet->sourceSequence = sourceSequence;
    // For a NACK, originalSender identifies the intended destination. This
    // keeps source matching unambiguous, while failedRouter identifies the
    // first-hop branch to avoid for transient retries.
    packet->originalSender = failedDestination;
    packet->finalTarget = target;
    packet->flags = finalReject
                        ? DTPK_FLAG_NACK_FINAL_REJECT
                        : DTPK_FLAG_NONE;
    packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    packet->failedRouter = finalReject
                               ? 0
                               : (failedRouter != 0
                                      ? failedRouter
                                      : MAC::getInstance()->getId());

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketNack),
        from,
        5000,
        0,
        true,
        false,
        nullptr,
        true);
}

void DTPK::sendAckPacket(uint16_t target, uint16_t from, uint16_t id,
                         uint16_t sourceSequence)
{
    DTPKPacketHeader *packet =
        static_cast<DTPKPacketHeader *>(malloc(sizeof(DTPKPacketHeader)));
    if (!packet)
        return;

    packet->type = ACK;
    packet->id = id;
    packet->sourceSequence = sourceSequence;
    packet->originalSender = MAC::getInstance()->getId();
    packet->finalTarget = target;
    packet->flags = DTPK_FLAG_NONE;
    packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketHeader),
        from,
        5000,
        0,
        true,
        false,
        nullptr,
        true);
}
