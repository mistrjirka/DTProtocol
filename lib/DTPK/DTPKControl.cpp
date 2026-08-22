#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

void DTPK::timeoutDeamon()
{
    if (_packetWaiting.empty())
        return;

    const uint32_t elapsed = _currentTime - _lastTick;
    std::vector<size_t> remove;

    for (size_t i = 0; i < _packetWaiting.size(); ++i)
    {
        DTPKPacketWaiting &waiting = _packetWaiting[i];

        if (waiting.gotAck)
        {
            if (waiting.callback)
            {
                const uint16_t ping = waiting.success
                    ? static_cast<uint16_t>(
                          waiting.timeout -
                          static_cast<uint32_t>(
                              std::max<int32_t>(waiting.timeLeft, 0)))
                    : 0;
                waiting.callback(waiting.success ? 1 : 0, ping);
            }
            remove.push_back(i);
            continue;
        }

        if (waiting.timeLeft <= 0)
        {
            if (waiting.callback)
                waiting.callback(0, 0);
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

    if (_packetWaiting.empty())
    {
        _waitingForAck = false;
        _currentlySendingId = 0;
    }
}

void DTPK::sendCrystPacket()
{
    if (_crystTimeout.sendingPacket)
        return;

    _crystTimeout.sendingPacket = true;
    _crystTimeout.remainingTimeToSend =
        static_cast<int32_t>(random(
            static_cast<long>(CRYST_JITTER_MIN_MS),
            static_cast<long>(CRYST_JITTER_MAX_MS + 1u)));
}

void DTPK::queueCrystSnapshot()
{
    // A newly generated state version supersedes queued HELLO and all unsent
    // chunks from older CRYST snapshots. The one chunk already handed to LCMM,
    // if any, is allowed to finish and the newer snapshot repairs it afterward.
    auto end = std::remove_if(
        _packetRequests.begin(),
        _packetRequests.end(),
        [](DTPKPacketRequest &request) {
            if (!request.packet)
                return false;
            const DTPKPacketType type = request.packet->type;
            if (type != HELLO && type != CRYST)
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
        packet->id = nextPacketId();
        packet->originSequence = _originSequence;
        packet->routeVersion = _routeVersion;
        packet->chunkIndex = chunk;
        packet->chunkCount = chunkCount;
        for (size_t i = 0; i < count; ++i)
            packet->neighbors[i] = routes[begin + i];

        addPacketToSendingQueue(
            reinterpret_cast<DTPKPacketUnknown *>(packet),
            bytes,
            BROADCAST,
            5000,
            0,
            false,
            false,
            nullptr,
            false);
    }
}

void DTPK::sendHello()
{
    if (MAC::getInstance()->getTransmitWaitMs() > 0)
        return;

    for (const DTPKPacketRequest &request : _packetRequests)
    {
        if (request.packet &&
            (request.packet->type == HELLO || request.packet->type == CRYST))
            return;
    }

    DTPKPacketHello *packet =
        static_cast<DTPKPacketHello *>(malloc(sizeof(DTPKPacketHello)));
    if (!packet)
        return;

    packet->type = HELLO;
    packet->id = nextPacketId();
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
    packet->id = nextPacketId();

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketCrystRequest),
        neighbor,
        3000,
        0,
        true,
        false,
        nullptr,
        true);
}

void DTPK::sendSeqRequest(uint16_t destination, uint16_t requestedSequence)
{
    DTPKPacketSeqRequest *packet =
        static_cast<DTPKPacketSeqRequest *>(malloc(sizeof(DTPKPacketSeqRequest)));
    if (!packet)
        return;

    packet->type = SEQ_REQ;
    packet->id = nextPacketId();
    packet->originalSender = MAC::getInstance()->getId();
    packet->destination = destination;
    packet->requestedSequence = requestedSequence == 0 ? 1 : requestedSequence;
    packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

    rememberSeqRequest(
        packet->originalSender,
        packet->id,
        packet->destination,
        packet->requestedSequence);

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        sizeof(DTPKPacketSeqRequest),
        BROADCAST,
        3000,
        0,
        false,
        false,
        nullptr,
        true);
}

void DTPK::sendNackPacket(uint16_t target, uint16_t from, uint16_t id,
                          uint16_t failedDestination)
{
    DTPKPacketHeader *packet =
        static_cast<DTPKPacketHeader *>(malloc(sizeof(DTPKPacketHeader)));
    if (!packet)
        return;

    packet->type = NACK_NOTFOUND;
    packet->id = id;
    // For a NACK, originalSender identifies the destination that could not be
    // reached. This makes {packet id, intended destination} matching unambiguous.
    packet->originalSender = failedDestination;
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

void DTPK::sendAckPacket(uint16_t target, uint16_t from, uint16_t id)
{
    DTPKPacketHeader *packet =
        static_cast<DTPKPacketHeader *>(malloc(sizeof(DTPKPacketHeader)));
    if (!packet)
        return;

    packet->type = ACK;
    packet->id = id;
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
