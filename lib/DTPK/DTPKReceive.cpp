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
    noteHeard(packet.frame->mac.sender);
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
    forwarded->hopLimit = static_cast<uint8_t>(request->hopLimit - 1u);

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(forwarded),
        sizeof(DTPKPacketSeqRequest),
        BROADCAST,
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
    if (packet.dtpkSize < sizeof(DTPKPacketGeneric))
        return false;

    DTPKPacketGeneric *generic =
        reinterpret_cast<DTPKPacketGeneric *>(packet.frame->data);

    if (generic->hopLimit <= 1)
    {
        if (generic->type == DATA_SINGLE)
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
        if (generic->type == DATA_SINGLE)
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
    if (outgoingSize < sizeof(DTPKPacketHeader) ||
        outgoingSize > DATASIZE_LCMM)
    {
        if (generic->type == DATA_SINGLE)
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

    memcpy(
        forwarded,
        packet.frame->data,
        outgoingSize);

    DTPKPacketGeneric *out =
        reinterpret_cast<DTPKPacketGeneric *>(forwarded);
    --out->hopLimit;

    if (generic->type == DATA_SINGLE)
        rememberData(generic->originalSender,
                     generic->sourceSequence,
                     generic->id);

    addPacketToSendingQueue(
        forwarded,
        outgoingSize,
        routing->router,
        5000,
        0,
        true,
        false,
        nullptr,
        generic->type == ACK || generic->type == NACK_NOTFOUND);
    return true;
}

void DTPK::receivingDeamon()
{
    if (_packetReceived.empty())
        return;

    auto packet = _packetReceived.front();
    _packetReceived.pop();

    DTPKPacketUnknown *dtpk =
        packet.frame
            ? reinterpret_cast<DTPKPacketUnknown *>(packet.frame->data)
            : nullptr;
    if (!dtpk || packet.dtpkSize < sizeof(DTPKPacketUnknown))
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
    case ACK:
    case NACK_NOTFOUND:
    {
        if (packet.dtpkSize < sizeof(DTPKPacketGeneric))
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
