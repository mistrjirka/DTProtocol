#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

void DTPK::parseHelloPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketHello))
        return;

    DTPKPacketHello *hello =
        reinterpret_cast<DTPKPacketHello *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    noteHeard(sender);

    auto applied = _neighborState.find(sender);
    bool needSnapshot = false;
    bool invalidateIndirect = false;

    if (applied == _neighborState.end())
    {
        needSnapshot = true;
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
        sendCrystRequest(sender);
}

void DTPK::parseCrystRequestPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketCrystRequest))
        return;

    DTPKPacketCrystRequest *request =
        reinterpret_cast<DTPKPacketCrystRequest *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    noteHeard(sender);

    bool acceptDigest = false;
    bool invalidateIndirect = false;
    const auto applied = _neighborState.find(sender);
    if (applied == _neighborState.end())
    {
        acceptDigest = true;
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
        invalidateIndirect = true;
        _neighborState.erase(sender);
        _crystAssemblies.erase(sender);
    }

    if (acceptDigest &&
        _crystDatabase.updateDirectNeighbor(
            sender, request->originSequence, invalidateIndirect))
        markRoutingChanged("cryst request direct route");

    // The requester already learned our digest from HELLO, and accepting this
    // digest establishes its direct route here. Both route changes schedule
    // snapshots; replying with another request would create a priority ping-pong
    // that can starve the very snapshots being requested.
    sendCrystPacket();
}

void DTPK::parseCrystPacket(
    const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketCryst))
        return;

    DTPKPacketCryst *cryst =
        reinterpret_cast<DTPKPacketCryst *>(packet.frame->data);
    const uint16_t sender = packet.frame->mac.sender;
    noteHeard(sender);

    if (cryst->chunkCount == 0 ||
        cryst->chunkCount > MAX_CRYST_CHUNKS ||
        cryst->chunkIndex >= cryst->chunkCount)
        return;

    const size_t payloadBytes = packet.dtpkSize - sizeof(DTPKPacketCryst);
    if ((payloadBytes % sizeof(NeighborRecordV2)) != 0)
        return;
    const size_t recordCount = payloadBytes / sizeof(NeighborRecordV2);

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
        {
            _originSequence = request->requestedSequence == 0
                                  ? 1
                                  : request->requestedSequence;
            ++_routeVersion;
            if (_routeVersion == 0)
                _routeVersion = 1;
            sendHello();
        }
        // Even an already-satisfied request may come from a node that missed the
        // current state. Re-send it without inventing a new generation.
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

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(forwarded),
        sizeof(DTPKPacketSeqRequest),
        directed ? nextHop : BROADCAST,
        3000,
        0,
        directed,
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
    if (!duplicate)
    {
        rememberData(data->originalSender, data->sourceSequence, data->id);
        if (_recieveCallback)
            _recieveCallback(
                data,
                static_cast<uint16_t>(
                    std::min<size_t>(packet.dtpkSize, UINT16_MAX)));
    }

    if ((data->flags & DTPK_FLAG_E2E_ACK_REQUESTED) != 0)
        sendAckPacket(
            data->originalSender,
            packet.frame->mac.sender,
            data->id,
            data->sourceSequence);
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

    RoutingRecord *routing = _crystDatabase.getRouting(generic->finalTarget);
    if (!routing)
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

    if (generic->type == DATA_SINGLE &&
        hasSeenData(generic->originalSender,
                    generic->sourceSequence,
                    generic->id))
        return false;

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

    if (generic->type == DATA_SINGLE)
        rememberData(generic->originalSender,
                     generic->sourceSequence,
                     generic->id);

    const bool priority =
        generic->type == ACK || generic->type == NACK_NOTFOUND ||
        generic->type == FRAGMENT_STATUS || generic->type == FRAGMENT_QUERY;
    addPacketToSendingQueue(
        forwarded,
        outgoingSize,
        routing->router,
        5000,
        0,
        true,
        false,
        nullptr,
        priority);
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

    noteHeard(packet.frame->mac.sender);

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
        if (packet.dtpkSize < sizeof(DTPKPacketHeader))
            break;
        DTPKPacketGeneric *generic =
            reinterpret_cast<DTPKPacketGeneric *>(dtpk);
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
        for (DTPKPacketWaiting &waiting : _packetWaiting)
        {
            if (waiting.id == generic->id &&
                waiting.sourceSequence == generic->sourceSequence &&
                waiting.target == generic->originalSender)
            {
                waiting.gotAck = true;
                waiting.success = success;
            }
        }
        break;
    }

    default:
        break;
    }

    free(packet.frame);
}
