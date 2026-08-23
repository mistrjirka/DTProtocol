#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>


bool DTPK::validateRoutedPacketShape(
    const ReceivedPacket &packet) const
{
    if (!packet.frame || packet.dtpkSize < sizeof(DTPKPacketHeader))
        return false;

    const DTPKPacketGeneric *generic =
        reinterpret_cast<const DTPKPacketGeneric *>(packet.frame->data);
    if (generic->id == 0 || generic->sourceSequence == 0 ||
        generic->originalSender == 0 || generic->finalTarget == 0 ||
        generic->hopLimit == 0)
        return false;

    const uint8_t applicationFlags = static_cast<uint8_t>(
        DTPK_FLAG_E2E_ACK_REQUESTED | DTPK_FLAG_COMPRESSED |
        DTPK_APPLICATION_FLAGS_MASK);

    switch (generic->type)
    {
    case DATA_SINGLE:
        return packet.dtpkSize >= sizeof(DTPKPacketGeneric) &&
               (generic->flags & static_cast<uint8_t>(~applicationFlags)) == 0;

    case DATA_FRAGMENT:
    {
        if (packet.dtpkSize < sizeof(DTPKPacketFragment) ||
            (generic->flags & static_cast<uint8_t>(~applicationFlags)) != 0)
            return false;
        const DTPKPacketFragment *fragment =
            reinterpret_cast<const DTPKPacketFragment *>(generic);
        const uint8_t count = fragmentCountForSize(fragment->totalSize);
        if (count == 0 || fragment->fragmentIndex >= count)
            return false;
        return packet.dtpkSize ==
               sizeof(DTPKPacketFragment) +
                   expectedFragmentBytes(
                       fragment->totalSize, fragment->fragmentIndex);
    }

    case FRAGMENT_QUERY:
    {
        if (packet.dtpkSize != sizeof(DTPKPacketFragmentQuery) ||
            (generic->flags & static_cast<uint8_t>(~applicationFlags)) != 0)
            return false;
        const DTPKPacketFragmentQuery *query =
            reinterpret_cast<const DTPKPacketFragmentQuery *>(generic);
        return query->queryId != 0 &&
               fragmentCountForSize(query->totalSize) != 0;
    }

    case FRAGMENT_STATUS:
    {
        if (packet.dtpkSize < sizeof(DTPKPacketFragmentStatus) ||
            generic->flags != DTPK_FLAG_NONE)
            return false;
        const DTPKPacketFragmentStatus *status =
            reinterpret_cast<const DTPKPacketFragmentStatus *>(generic);
        if (status->fragmentCount == 0)
            return false;
        const size_t bitmapBytes =
            (static_cast<size_t>(status->fragmentCount) + 7u) / 8u;
        return packet.dtpkSize ==
               sizeof(DTPKPacketFragmentStatus) + bitmapBytes;
    }

    case ACK:
        return packet.dtpkSize == sizeof(DTPKPacketHeader) &&
               generic->flags == DTPK_FLAG_NONE;
    case NACK_NOTFOUND:
    {
        if ((generic->flags & static_cast<uint8_t>(
                 ~DTPK_FLAG_NACK_FINAL_REJECT)) != 0 ||
            (packet.dtpkSize != sizeof(DTPKPacketHeader) &&
             packet.dtpkSize != sizeof(DTPKPacketNack)))
            return false;
        if (packet.dtpkSize == sizeof(DTPKPacketNack))
        {
            const DTPKPacketNack *nack =
                reinterpret_cast<const DTPKPacketNack *>(generic);
            const bool finalReject =
                (generic->flags & DTPK_FLAG_NACK_FINAL_REJECT) != 0;
            if ((!finalReject && nack->failedRouter == 0) ||
                (finalReject && nack->failedRouter != 0))
                return false;
        }
        return true;
    }

    default:
        return false;
    }
}

bool DTPK::applicationOriginIsStale(
    const DTPKPacketGeneric *packet) const
{
    if (!packet || packet->originalSender == 0 ||
        packet->sourceSequence == 0)
        return true;
    return _crystDatabase.hasNewerKnownSequence(
        packet->originalSender, packet->sourceSequence);
}

void DTPK::learnDirectApplicationOrigin(
    const DTPKPacketGeneric *packet,
    uint16_t immediateSender)
{
    if (!packet || immediateSender == 0 ||
        immediateSender == MAC::getInstance()->getId() ||
        packet->originalSender != immediateSender ||
        packet->sourceSequence == 0)
        return;

    bool requestSnapshot = false;
    bool invalidateIndirect = false;
    auto applied = _neighborState.find(immediateSender);
    if (applied == _neighborState.end())
    {
        RoutingRecord *known = _crystDatabase.getRouting(immediateSender);
        const bool alreadyLearnedDirectly =
            known && known->router == immediateSender &&
            known->distance == 1 &&
            known->sequence == packet->sourceSequence;
        if (alreadyLearnedDirectly)
            return; // first DATA/HELLO already installed and requested repair

        // A valid directly received application packet proves a usable
        // bidirectional link even if HELLO/CRYST crossed or was lost. Keep the
        // direct route immediately, but request the still-missing full snapshot.
        requestSnapshot = true;
        invalidateIndirect = true;
    }
    else if (packet->sourceSequence == applied->second.originSequence)
    {
        // Route version zero marks a direct route learned provisionally from
        // DATA. If its first reliable CRYST_REQ was lost, later valid DATA from
        // the same incarnation is fresh evidence and must retry synchronization.
        requestSnapshot = applied->second.routeVersion == 0;
    }
    else if (sequenceNewer(
                 packet->sourceSequence, applied->second.originSequence))
    {
        // Direct DATA from a newer boot incarnation is stronger evidence than
        // stale indirect state contributed by the previous incarnation.
        requestSnapshot = true;
        invalidateIndirect = true;
        _neighborState.erase(applied);
        _crystAssemblies.erase(immediateSender);
    }
    else
    {
        // Delayed application data from an older incarnation must never
        // downgrade a currently committed neighbor generation.
        return;
    }

    if (_crystDatabase.updateDirectNeighbor(
            immediateSender,
            packet->sourceSequence,
            invalidateIndirect))
        markRoutingChanged("direct application origin");

    if (requestSnapshot)
    {
        // Record a provisional incarnation immediately. Route version zero is
        // intentionally below every valid advertised snapshot version, so a
        // matching HELLO/CRYST still completes state while delayed control from
        // an older boot cannot downgrade the direct route learned from DATA.
        _neighborState[immediateSender] =
            NeighborState{packet->sourceSequence, 0};
        sendCrystRequest(immediateSender);
    }
}

void DTPK::rememberReverseBreadcrumb(
    const DTPKPacketGeneric *packet,
    uint16_t immediateSender)
{
    if (!packet || immediateSender == 0 ||
        immediateSender == MAC::getInstance()->getId() ||
        packet->originalSender == 0 || packet->sourceSequence == 0 ||
        packet->id == 0 || packet->hopLimit == 0)
        return;

    // DATA starts with hopLimit 255. Every relay decrements it once, so the
    // received value gives an exact strictly decreasing return-path metric.
    const uint16_t distance =
        static_cast<uint16_t>(DTPK_DEFAULT_HOP_LIMIT) + 1u -
        static_cast<uint16_t>(packet->hopLimit);
    if (distance == 0 || distance >= DTPK_ROUTE_INFINITY)
        return;

    ReverseBreadcrumb *slot = nullptr;
    for (ReverseBreadcrumb &entry : _reverseBreadcrumbs)
    {
        if (entry.originalSender == packet->originalSender &&
            entry.sourceSequence == packet->sourceSequence &&
            entry.id == packet->id)
        {
            slot = &entry;
            break;
        }
        if (!slot &&
            (entry.originalSender == 0 ||
             static_cast<uint32_t>(_currentTime - entry.lastUpdate) >=
                 REVERSE_BREADCRUMB_EXPIRY_MS))
            slot = &entry;
    }

    if (!slot)
    {
        slot = &_reverseBreadcrumbs[_reverseBreadcrumbNext];
        _reverseBreadcrumbNext =
            (_reverseBreadcrumbNext + 1u) % REVERSE_BREADCRUMB_CACHE_SIZE;
    }

    slot->originalSender = packet->originalSender;
    slot->sourceSequence = packet->sourceSequence;
    slot->id = packet->id;
    slot->previousHop = immediateSender;
    slot->distance = static_cast<uint8_t>(distance);
    slot->lastUpdate = _currentTime;
}

bool DTPK::findReverseBreadcrumb(
    uint16_t originalSender,
    uint16_t sourceSequence,
    uint16_t id,
    uint16_t &previousHop) const
{
    for (const ReverseBreadcrumb &entry : _reverseBreadcrumbs)
    {
        if (entry.originalSender != originalSender ||
            entry.sourceSequence != sourceSequence || entry.id != id ||
            entry.previousHop == 0 ||
            static_cast<uint32_t>(_currentTime - entry.lastUpdate) >=
                REVERSE_BREADCRUMB_EXPIRY_MS)
            continue;
        previousHop = entry.previousHop;
        return true;
    }
    return false;
}

bool DTPK::findRecentReverseRoute(
    uint16_t destination,
    uint16_t maxDistanceExclusive,
    RoutingRecord &result) const
{
    const ReverseBreadcrumb *best = nullptr;
    for (const ReverseBreadcrumb &entry : _reverseBreadcrumbs)
    {
        if (entry.originalSender != destination || entry.previousHop == 0 ||
            entry.distance == 0 || entry.distance >= maxDistanceExclusive ||
            static_cast<uint32_t>(_currentTime - entry.lastUpdate) >=
                REVERSE_BREADCRUMB_EXPIRY_MS)
            continue;

        if (!best ||
            sequenceNewer(entry.sourceSequence, best->sourceSequence) ||
            (entry.sourceSequence == best->sourceSequence &&
             (entry.distance < best->distance ||
              (entry.distance == best->distance &&
               static_cast<uint32_t>(_currentTime - entry.lastUpdate) <
                   static_cast<uint32_t>(_currentTime - best->lastUpdate)))))
            best = &entry;
    }

    if (!best)
        return false;
    result.router = best->previousHop;
    result.distance = best->distance;
    result.sequence = best->sourceSequence;
    return true;
}

bool DTPK::resolveRoute(
    uint16_t destination,
    uint16_t maxDistanceExclusive,
    RoutingRecord &result,
    bool &fromReversePath) const
{
    const RoutingRecord *selected = _crystDatabase.getRouting(destination);
    if (selected && selected->distance < maxDistanceExclusive)
    {
        result = *selected;
        fromReversePath = false;
        return true;
    }

    if (findRecentReverseRoute(destination, maxDistanceExclusive, result))
    {
        fromReversePath = true;
        return true;
    }
    return false;
}

void DTPK::expireReverseBreadcrumbs()
{
    for (ReverseBreadcrumb &entry : _reverseBreadcrumbs)
    {
        if (entry.originalSender != 0 &&
            static_cast<uint32_t>(_currentTime - entry.lastUpdate) >=
                REVERSE_BREADCRUMB_EXPIRY_MS)
            entry = ReverseBreadcrumb{};
    }
}

void DTPK::parseHelloPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketHello))
        return;

    DTPKPacketHello *hello =
        reinterpret_cast<DTPKPacketHello *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    if (hello->originSequence == 0 || hello->routeVersion == 0)
        return;
    noteHeard(sender);

    auto applied = _neighborState.find(sender);
    bool needSnapshot = false;
    bool provisionalState = false;
    bool invalidateIndirect = false;

    if (applied == _neighborState.end())
    {
        needSnapshot = true;
        provisionalState = true;
        invalidateIndirect = true;
    }
    else if (hello->originSequence == applied->second.originSequence)
    {
        if (versionNewer(hello->routeVersion, applied->second.routeVersion))
            needSnapshot = true;
        else if (hello->routeVersion != applied->second.routeVersion)
            return; // stale HELLO
    }
    else if (sequenceNewer(
                 hello->originSequence, applied->second.originSequence))
    {
        needSnapshot = true;
        provisionalState = true;
        invalidateIndirect = true;
        _neighborState.erase(applied);
        _crystAssemblies.erase(sender);
    }
    else
    {
        return; // stale incarnation
    }

    if (_crystDatabase.updateDirectNeighbor(
            sender, hello->originSequence, invalidateIndirect))
        markRoutingChanged("hello direct route");

    if (needSnapshot)
    {
        if (provisionalState)
            _neighborState[sender] =
                NeighborState{hello->originSequence, 0};
        sendCrystRequest(sender);
    }
}

void DTPK::parseCrystRequestPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketCrystRequest))
        return;

    DTPKPacketCrystRequest *request =
        reinterpret_cast<DTPKPacketCrystRequest *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    if (request->originSequence == 0 || request->routeVersion == 0)
        return;
    noteHeard(sender);

    bool acceptDigest = false;
    bool provisionalState = false;
    bool invalidateIndirect = false;
    const auto applied = _neighborState.find(sender);
    if (applied == _neighborState.end())
    {
        acceptDigest = true;
        provisionalState = true;
        invalidateIndirect = true;
    }
    else if (request->originSequence == applied->second.originSequence)
    {
        // A request may arrive before its matching snapshot. Equal/newer state
        // is useful; an older wrapped version is ignored.
        acceptDigest =
            request->routeVersion == applied->second.routeVersion ||
            versionNewer(request->routeVersion, applied->second.routeVersion);
    }
    else if (sequenceNewer(
                 request->originSequence, applied->second.originSequence))
    {
        acceptDigest = true;
        provisionalState = true;
        invalidateIndirect = true;
        _neighborState.erase(sender);
        _crystAssemblies.erase(sender);
    }

    if (acceptDigest)
    {
        if (_crystDatabase.updateDirectNeighbor(
                sender, request->originSequence, invalidateIndirect))
            markRoutingChanged("cryst request direct route");
        if (provisionalState)
            _neighborState[sender] =
                NeighborState{request->originSequence, 0};
    }

    // Answer the requester directly and reliably. Ordinary route propagation
    // remains jittered broadcast traffic, but a repair transaction must not
    // depend on another independently losable broadcast or its random delay.
    // Each chunk uses the existing LCMM ACK/retry path and same-target queued
    // responses are coalesced to the newest snapshot.
    queueCrystSnapshot(sender, true);
}

void DTPK::parseCrystPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketCryst))
        return;

    DTPKPacketCryst *cryst =
        reinterpret_cast<DTPKPacketCryst *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;

    if (cryst->originSequence == 0 || cryst->routeVersion == 0 ||
        cryst->chunkCount == 0 ||
        cryst->chunkCount > MAX_CRYST_CHUNKS ||
        cryst->chunkIndex >= cryst->chunkCount)
        return;

    const size_t payloadBytes = packet.dtpkSize - sizeof(DTPKPacketCryst);
    if ((payloadBytes % sizeof(NeighborRecordV2)) != 0)
        return;
    const size_t recordCount = payloadBytes / sizeof(NeighborRecordV2);
    for (size_t index = 0; index < recordCount; ++index)
    {
        const NeighborRecordV2 &record = cryst->neighbors[index];
        if (record.id == 0 || record.id == sender ||
            record.sequence == 0 || record.distance == 0 ||
            record.distance >= DTPK_ROUTE_INFINITY - 1u)
            return;
        for (size_t previous = 0; previous < index; ++previous)
        {
            if (cryst->neighbors[previous].id == record.id)
                return;
        }
    }
    noteHeard(sender);

    auto applied = _neighborState.find(sender);
    if (applied != _neighborState.end())
    {
        if (cryst->originSequence == applied->second.originSequence)
        {
            if (cryst->routeVersion == applied->second.routeVersion)
                return;
            if (!versionNewer(
                    cryst->routeVersion, applied->second.routeVersion))
                return;
        }
        else if (!sequenceNewer(
                     cryst->originSequence, applied->second.originSequence))
        {
            return;
        }
    }

    CrystAssembly &assembly = _crystAssemblies[sender];
    const bool different =
        assembly.chunkCount == 0 ||
        assembly.originSequence != cryst->originSequence ||
        assembly.routeVersion != cryst->routeVersion ||
        assembly.chunkCount != cryst->chunkCount;

    // Do not let an older interleaved snapshot replace a newer in-progress
    // assembly merely because neither one has been committed yet.
    if (assembly.chunkCount != 0 && different)
    {
        if (cryst->originSequence == assembly.originSequence)
        {
            if (cryst->routeVersion != assembly.routeVersion &&
                !versionNewer(cryst->routeVersion, assembly.routeVersion))
                return;
        }
        else if (!sequenceNewer(
                     cryst->originSequence, assembly.originSequence))
        {
            return;
        }
    }

    if (different)
    {
        assembly.originSequence = cryst->originSequence;
        assembly.routeVersion = cryst->routeVersion;
        assembly.chunkCount = cryst->chunkCount;
        assembly.chunks.assign(cryst->chunkCount, {});
        assembly.received.assign(cryst->chunkCount, 0);
    }

    assembly.lastUpdate = _currentTime;
    if (!assembly.received[cryst->chunkIndex])
    {
        assembly.chunks[cryst->chunkIndex].assign(
            cryst->neighbors, cryst->neighbors + recordCount);
        assembly.received[cryst->chunkIndex] = 1;
    }

    if (std::find(
            assembly.received.begin(), assembly.received.end(), 0) !=
        assembly.received.end())
        return;

    std::vector<NeighborRecordV2> records;
    size_t total = 0;
    for (const auto &chunk : assembly.chunks)
        total += chunk.size();
    records.reserve(total);
    for (const auto &chunk : assembly.chunks)
        records.insert(records.end(), chunk.begin(), chunk.end());

    const uint16_t originSequence = assembly.originSequence;
    const uint32_t routeVersion = assembly.routeVersion;
    _crystAssemblies.erase(sender);

    std::sort(
        records.begin(),
        records.end(),
        [](const NeighborRecordV2 &left, const NeighborRecordV2 &right) {
            return left.id < right.id;
        });
    if (std::adjacent_find(
            records.begin(),
            records.end(),
            [](const NeighborRecordV2 &left, const NeighborRecordV2 &right) {
                return left.id == right.id;
            }) != records.end())
        return;

    const bool changed = _crystDatabase.updateFromCrystPacket(
        sender,
        originSequence,
        records.empty() ? nullptr : records.data(),
        records.size());

    _neighborState[sender] = NeighborState{originSequence, routeVersion};

    if (changed)
        markRoutingChanged("cryst snapshot");
    else
        processSequenceRequests();
}

void DTPK::parseSeqRequestPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketSeqRequest))
        return;

    DTPKPacketSeqRequest *request =
        reinterpret_cast<DTPKPacketSeqRequest *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    if (request->id == 0 || request->originalSender == 0 ||
        request->destination == 0 || request->requestedSequence == 0 ||
        request->hopLimit == 0 ||
        (request->flags & static_cast<uint8_t>(~DTPK_SEQ_REQ_FLOOD)) != 0)
        return;
    noteHeard(sender);

    if (hasSeenSeqRequest(
            request->originalSender,
            request->id,
            request->destination,
            request->requestedSequence))
        return;

    rememberSeqRequest(
        request->originalSender,
        request->id,
        request->destination,
        request->requestedSequence);

    if (request->destination == MAC::getInstance()->getId())
    {
        if (sequenceNewer(request->requestedSequence, _originSequence))
            requestOriginSequenceAdvance(request->requestedSequence);
        // Even an already-satisfied or temporarily deferred request may come
        // from a node that missed state. Re-send current state now; a deferred
        // newer generation is advertised when the active DATA boundary closes.
        sendCrystPacket();
        return;
    }

    if (request->hopLimit <= 1)
        return;

    DTPKPacketSeqRequest *forwarded =
        static_cast<DTPKPacketSeqRequest *>(
            malloc(sizeof(DTPKPacketSeqRequest)));
    if (!forwarded)
        return;

    forwarded->type = SEQ_REQ;
    forwarded->id = request->id;
    forwarded->originalSender = request->originalSender;
    forwarded->destination = request->destination;
    forwarded->requestedSequence = request->requestedSequence;
    forwarded->flags = request->flags;
    forwarded->hopLimit = static_cast<uint8_t>(request->hopLimit - 1u);

    uint16_t nextHop = BROADCAST;
    const bool directed =
        (request->flags & DTPK_SEQ_REQ_FLOOD) == 0 &&
        _crystDatabase.getRepairNextHop(
            request->destination,
            sender,
            nextHop);
    if (!directed)
        forwarded->flags |= DTPK_SEQ_REQ_FLOOD;

    // Forward this logical repair wave once. The originating node persists
    // and retries the end-to-end request; adding five LCMM attempts at each hop
    // creates multiplicative control traffic on a congested half-duplex mesh.
    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(forwarded),
        sizeof(DTPKPacketSeqRequest),
        directed ? nextHop : BROADCAST,
        3000,
        0,
        false,
        false,
        nullptr,
        true);
}

void DTPK::parseSingleDataPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketGeneric))
        return;

    DTPKPacketGeneric *data =
        reinterpret_cast<DTPKPacketGeneric *>(packet.frame->data);

    const bool duplicate = hasSeenData(
        data->originalSender, data->sourceSequence, data->id);
    bool delivered = duplicate;
    if (!duplicate)
    {
        delivered = deliverApplicationPacket(data, packet.dtpkSize);
        if (delivered)
            rememberData(data->originalSender, data->sourceSequence, data->id);
    }

    if ((data->flags & DTPK_FLAG_E2E_ACK_REQUESTED) != 0)
    {
        if (delivered)
            sendAckPacket(
                data->originalSender,
                packet.frame->mac.sender,
                data->id,
                data->sourceSequence);
        else
            sendNackPacket(
                data->originalSender,
                packet.frame->mac.sender,
                data->id,
                data->sourceSequence,
                data->finalTarget,
                true);
    }
}

bool DTPK::forwardRoutedPacket(const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketHeader))
        return false;

    DTPKPacketGeneric *generic =
        reinterpret_cast<DTPKPacketGeneric *>(packet.frame->data);
    const bool applicationData =
        generic->type == DATA_SINGLE || generic->type == DATA_FRAGMENT;

    if (generic->hopLimit <= 1)
    {
        if (applicationData)
            sendNackPacket(
                generic->originalSender,
                packet.frame->mac.sender,
                generic->id,
                generic->sourceSequence,
                generic->finalTarget);
        return false;
    }

    uint16_t nextHop = 0;
    const bool transactionResponse =
        generic->type == ACK || generic->type == NACK_NOTFOUND ||
        generic->type == FRAGMENT_STATUS;
    bool haveRoute = transactionResponse &&
        findReverseBreadcrumb(
            generic->finalTarget,
            generic->sourceSequence,
            generic->id,
            nextHop);
    if (!haveRoute)
    {
        RoutingRecord route{};
        bool reversePath = false;
        haveRoute = resolveRoute(
            generic->finalTarget,
            generic->hopLimit,
            route,
            reversePath);
        if (haveRoute)
            nextHop = route.router;
    }
    if (!haveRoute || nextHop == 0)
    {
        if (applicationData)
            sendNackPacket(
                generic->originalSender,
                packet.frame->mac.sender,
                generic->id,
                generic->sourceSequence,
                generic->finalTarget);
        return false;
    }

    if (generic->type == DATA_SINGLE)
    {
        // Same-identity E2E retries are intentional, but a relay needs only one
        // queued copy at a time. This prevents a broken downstream link from
        // turning source retries into an unbounded local forwarding backlog.
        for (const DTPKPacketRequest &queued : _packetRequests)
        {
            if (!queued.packet || queued.packet->type != DATA_SINGLE)
                continue;
            const DTPKPacketGeneric *existing =
                reinterpret_cast<const DTPKPacketGeneric *>(queued.packet);
            if (existing->id == generic->id &&
                existing->sourceSequence == generic->sourceSequence &&
                existing->originalSender == generic->originalSender &&
                existing->finalTarget == generic->finalTarget)
                return true;
        }
    }

    const size_t outgoingSize = packet.dtpkSize;
    if (outgoingSize > DATASIZE_LCMM)
    {
        if (applicationData)
            sendNackPacket(
                generic->originalSender,
                packet.frame->mac.sender,
                generic->id,
                generic->sourceSequence,
                generic->finalTarget);
        return false;
    }

    DTPKPacketUnknown *forwarded =
        static_cast<DTPKPacketUnknown *>(malloc(outgoingSize));
    if (!forwarded)
        return false;
    memcpy(forwarded, packet.frame->data, outgoingSize);

    DTPKPacketGeneric *out =
        reinterpret_cast<DTPKPacketGeneric *>(forwarded);
    --out->hopLimit;
    if (out->type == NACK_NOTFOUND &&
        (out->flags & DTPK_FLAG_NACK_FINAL_REJECT) == 0 &&
        outgoingSize == sizeof(DTPKPacketNack))
    {
        DTPKPacketNack *nack =
            reinterpret_cast<DTPKPacketNack *>(out);
        nack->failedRouter = MAC::getInstance()->getId();
    }

    const bool priority =
        generic->type == ACK || generic->type == NACK_NOTFOUND ||
        generic->type == FRAGMENT_STATUS || generic->type == FRAGMENT_QUERY;
    addPacketToSendingQueue(
        forwarded,
        outgoingSize,
        nextHop,
        5000,
        0,
        true,
        false,
        nullptr,
        priority,
        applicationData ? packet.frame->mac.sender : 0);
    return true;
}

void DTPK::receivingDeamon()
{
    if (_packetReceived.empty())
        return;

    ReceivedPacket packet = _packetReceived.front();
    _packetReceived.pop();

    DTPKPacketUnknown *dtpk =
        packet.frame
            ? reinterpret_cast<DTPKPacketUnknown *>(packet.frame->data)
            : nullptr;
    if (!dtpk || packet.dtpkSize < sizeof(DTPKPacketUnknown) ||
        !dtpkWireVersionSupported(static_cast<uint8_t>(dtpk->type)))
    {
        free(packet.frame);
        return;
    }

    switch (dtpk->type)
    {
    case HELLO:
        parseHelloPacket(packet);
        break;
    case CRYST:
        parseCrystPacket(packet);
        break;
    case CRYST_REQ:
        parseCrystRequestPacket(packet);
        break;
    case SEQ_REQ:
        parseSeqRequestPacket(packet);
        break;

    case DATA_SINGLE:
    case DATA_FRAGMENT:
    case FRAGMENT_STATUS:
    case FRAGMENT_QUERY:
    case ACK:
    case NACK_NOTFOUND:
    {
        if (!validateRoutedPacketShape(packet))
            break;
        noteHeard(packet.frame->mac.sender);
        DTPKPacketGeneric *generic =
            reinterpret_cast<DTPKPacketGeneric *>(dtpk);
        if (generic->type == DATA_SINGLE ||
            generic->type == DATA_FRAGMENT ||
            generic->type == FRAGMENT_QUERY)
        {
            if (applicationOriginIsStale(generic))
                break;
            rememberReverseBreadcrumb(
                generic, packet.frame->mac.sender);
            learnDirectApplicationOrigin(
                generic, packet.frame->mac.sender);
        }
        if (generic->finalTarget != MAC::getInstance()->getId())
        {
            forwardRoutedPacket(packet);
            break;
        }

        if (generic->type == DATA_SINGLE)
        {
            parseSingleDataPacket(packet);
            break;
        }
        if (generic->type == DATA_FRAGMENT)
        {
            parseFragmentPacket(packet);
            break;
        }
        if (generic->type == FRAGMENT_STATUS)
        {
            parseFragmentStatusPacket(packet);
            break;
        }
        if (generic->type == FRAGMENT_QUERY)
        {
            parseFragmentQueryPacket(packet);
            break;
        }

        const bool success = generic->type == ACK;
        const bool finalReject =
            generic->type == NACK_NOTFOUND &&
            (generic->flags & DTPK_FLAG_NACK_FINAL_REJECT) != 0;
        uint16_t failedRouter = 0;
        if (generic->type == NACK_NOTFOUND &&
            packet.dtpkSize == sizeof(DTPKPacketNack))
        {
            failedRouter =
                reinterpret_cast<DTPKPacketNack *>(generic)->failedRouter;
        }
        for (DTPKPacketWaiting &waiting : _packetWaiting)
        {
            if (waiting.id != generic->id ||
                waiting.sourceSequence != generic->sourceSequence ||
                waiting.target != generic->originalSender)
                continue;

            if (success || finalReject)
            {
                waiting.gotAck = true;
                waiting.success = success;
            }
            else
            {
                // A relay could not continue along its selected route. Keep the
                // original E2E deadline and retry the same identity as soon as
                // crystallization exposes a replacement next hop.
                waiting.retryQueued = false;
                waiting.routeRepairNeeded = true;
                waiting.failedRouter =
                    failedRouter != 0 ? failedRouter : waiting.lastRouter;
                waiting.nextRetryAt =
                    _currentTime + singleLinkFailureBackoffMs(
                        waiting.retryCount);
            }
        }
        break;
    }

    default:
        break;
    }

    free(packet.frame);
}
