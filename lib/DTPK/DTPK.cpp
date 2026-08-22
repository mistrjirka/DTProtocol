#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

DTPK *DTPK::dtpk = nullptr;

void DTPK::initialize(uint8_t KLimit, uint16_t originSequence)
{
    if (!dtpk)
        dtpk = new DTPK(KLimit, originSequence);
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

bool DTPK::versionNewer(uint32_t a, uint32_t b)
{
    if (a == b)
        return false;
    return static_cast<uint32_t>(a - b) < 0x80000000u;
}

bool DTPK::isControlType(DTPKPacketType type)
{
    return type == CRYST || type == HELLO || type == CRYST_REQ ||
           type == SEQ_REQ || type == ACK || type == NACK_NOTFOUND;
}

DTPK::DTPK(uint8_t KLimit, uint16_t originSequence)
    : _crystDatabase(MAC::getInstance()->getId())
{
    _Klimit = KLimit;
    _packetCounter = 0;
    _timeOfInit = millis();
    _currentTime = _timeOfInit;
    _lastTick = _currentTime;

    _seed = MathExtension.murmur64(
        (static_cast<uint64_t>(MAC::getInstance()->random()) << 32) |
        MAC::getInstance()->random());
    randomSeed(_seed);

    _originSequence = originSequence == 0 ? 1 : originSequence;
    _routeVersion = 1;
    _helloRemaining = static_cast<int32_t>(random(100, 1000));
    _maintenanceRemaining = static_cast<int32_t>(MAINTENANCE_PERIOD_MS);

    _crystTimeout.sendingPacket = false;
    _crystTimeout.remainingTimeToSend = 0;

    _waitingForAck = false;
    _currentlySendingId = 0;

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
    return _packetCounter++;
}

bool DTPK::hasSeenData(uint16_t originalSender, uint16_t id) const
{
    for (const PacketIdentity &entry : _recentData)
    {
        if (entry.valid && entry.originalSender == originalSender && entry.id == id)
            return true;
    }
    return false;
}

void DTPK::rememberData(uint16_t originalSender, uint16_t id)
{
    if (hasSeenData(originalSender, id))
        return;
    PacketIdentity &entry = _recentData[_recentDataNext];
    entry.originalSender = originalSender;
    entry.id = id;
    entry.valid = true;
    _recentDataNext = (_recentDataNext + 1) % RECENT_DATA_CACHE_SIZE;
}

bool DTPK::hasSeenSeqRequest(uint16_t originalSender, uint16_t id,
                             uint16_t destination, uint16_t requestedSequence) const
{
    for (const SeqRequestIdentity &entry : _recentSeqRequests)
    {
        if (entry.valid &&
            entry.originalSender == originalSender &&
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
    entry.valid = true;
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
}

void DTPK::expireNeighbours()
{
    std::vector<uint16_t> stale;
    stale.reserve(_lastHeard.size());
    for (const auto &entry : _lastHeard)
    {
        if (static_cast<uint32_t>(_currentTime - entry.second) >= NEIGHBOR_EXPIRY_MS)
            stale.push_back(entry.first);
    }

    for (uint16_t neighbor : stale)
    {
        _lastHeard.erase(neighbor);
        _neighborState.erase(neighbor);
        _crystAssemblies.erase(neighbor);
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
        if (route != nullptr &&
            !sequenceNewer(pending.requestedSequence, route->sequence))
        {
            it = _pendingSeqRequests.erase(it);
            continue;
        }

        if (pending.attempts >= SEQ_REQ_MAX_ATTEMPTS)
        {
            it = _pendingSeqRequests.erase(it);
            continue;
        }

        const bool due =
            pending.attempts == 0 ||
            static_cast<uint32_t>(_currentTime - pending.lastSent) >=
                SEQ_REQ_COOLDOWN_MS;
        if (due)
        {
            sendSeqRequest(destination, pending.requestedSequence);
            pending.lastSent = _currentTime;
            ++pending.attempts;
        }
        ++it;
    }
}

void DTPK::addPacketToSendingQueue(
    DTPKPacketUnknown *packet,
    size_t size,
    uint16_t target,
    int16_t timeout,
    int16_t timeLeftToSend,
    bool lcmmAck,
    bool dtpkAck,
    PacketAckCallback callback,
    bool priority)
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

    DTPKPacketRequest request{
        packet,
        size,
        target,
        timeout,
        timeLeftToSend,
        lcmmAck,
        dtpkAck,
        callback};

    if (priority || packet->type == ACK || packet->type == NACK_NOTFOUND ||
        packet->type == CRYST_REQ || packet->type == SEQ_REQ)
        _packetRequests.insert(_packetRequests.begin(), request);
    else
        _packetRequests.push_back(request);
}

void DTPK::sendingDeamon()
{
    if (_packetRequests.empty())
        return;

    const uint32_t elapsed = _currentTime - _lastTick;
    for (size_t i = 0; i < _packetRequests.size(); ++i)
    {
        DTPKPacketRequest &request = _packetRequests[i];

        if (request.timeLeftToSend > 0)
        {
            request.timeLeftToSend =
                elapsed >= static_cast<uint32_t>(request.timeLeftToSend)
                    ? 0
                    : request.timeLeftToSend - static_cast<int32_t>(elapsed);
        }

        const DTPKPacketType type = request.packet->type;
        if (request.timeLeftToSend > 0 ||
            LCMM::getInstance()->isSending() ||
            (_waitingForAck && !isControlType(type)))
            continue;

        const uint16_t lcmmId = LCMM::getInstance()->sendPacketSingle(
            request.lcmmAck,
            request.target,
            reinterpret_cast<unsigned char *>(request.packet),
            static_cast<uint8_t>(request.size),
            DTPK::receiveAck,
            request.timeout > 0
                ? static_cast<uint32_t>(std::max<int32_t>(1, request.timeout / 3))
                : 1u,
            3);

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

            if (request.callback)
                request.callback(0, 0);
            free(request.packet);
            _packetRequests.erase(
                _packetRequests.begin() + static_cast<long>(i));
            return;
        }

        if (request.dtpkAck)
        {
            const DTPKPacketGeneric *generic =
                reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
            DTPKPacketWaiting waiting{};
            waiting.id = request.packet->id;
            waiting.target = generic->finalTarget;
            waiting.timeout =
                request.timeout > 0 ? static_cast<uint32_t>(request.timeout) : 1u;
            waiting.timeLeft = request.timeout > 0 ? request.timeout : 1;
            waiting.gotAck = false;
            waiting.success = false;
            waiting.callback = request.callback;
            _packetWaiting.push_back(waiting);
            _waitingForAck = true;
            _currentlySendingId = waiting.id;
        }

        free(request.packet);
        _packetRequests.erase(_packetRequests.begin() + static_cast<long>(i));
        return;
    }
}

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

void DTPK::parseHelloPacket(
    std::pair<DTPKPacketUnknownReceive *, size_t> packet)
{
    if (packet.second < sizeof(DTPKPacketHelloReceive))
        return;

    DTPKPacketHelloReceive *hello =
        reinterpret_cast<DTPKPacketHelloReceive *>(packet.first);
    const uint16_t sender = hello->lcmm.mac.sender;
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
            return;
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
        return;
    }

    if (_crystDatabase.updateDirectNeighbor(
            sender, hello->originSequence, invalidateIndirect))
        markRoutingChanged("hello direct route");

    if (needSnapshot)
        sendCrystRequest(sender);
}

void DTPK::parseCrystRequestPacket(
    std::pair<DTPKPacketUnknownReceive *, size_t> packet)
{
    if (packet.second < sizeof(DTPKPacketCrystRequestReceive))
        return;
    DTPKPacketCrystRequestReceive *request =
        reinterpret_cast<DTPKPacketCrystRequestReceive *>(packet.first);
    noteHeard(request->lcmm.mac.sender);
    sendCrystPacket();
}

void DTPK::parseCrystPacket(
    std::pair<DTPKPacketUnknownReceive *, size_t> packet)
{
    if (packet.second < sizeof(DTPKPacketCrystReceive))
        return;

    DTPKPacketCrystReceive *cryst =
        reinterpret_cast<DTPKPacketCrystReceive *>(packet.first);
    const uint16_t sender = cryst->lcmm.mac.sender;
    noteHeard(sender);

    if (cryst->chunkCount == 0 ||
        cryst->chunkCount > MAX_CRYST_CHUNKS ||
        cryst->chunkIndex >= cryst->chunkCount)
        return;

    const size_t payloadBytes = packet.second - sizeof(DTPKPacketCrystReceive);
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
    std::pair<DTPKPacketUnknownReceive *, size_t> packet)
{
    if (packet.second < sizeof(DTPKPacketSeqRequestReceive))
        return;

    DTPKPacketSeqRequestReceive *request =
        reinterpret_cast<DTPKPacketSeqRequestReceive *>(packet.first);
    const uint16_t sender = request->lcmm.mac.sender;
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
    std::pair<DTPKPacketUnknownReceive *, size_t> packet)
{
    if (packet.second < sizeof(DTPKPacketGenericReceive))
        return;

    DTPKPacketGenericReceive *data =
        reinterpret_cast<DTPKPacketGenericReceive *>(packet.first);

    const bool duplicate = hasSeenData(data->originalSender, data->id);
    if (!duplicate)
    {
        rememberData(data->originalSender, data->id);
        if (_recieveCallback)
            _recieveCallback(
                data,
                static_cast<uint16_t>(
                    std::min<size_t>(packet.second, UINT16_MAX)));
    }

    if ((data->flags & DTPK_FLAG_E2E_ACK_REQUESTED) != 0)
        sendAckPacket(
            data->originalSender,
            data->lcmm.mac.sender,
            data->id);
}

bool DTPK::forwardRoutedPacket(
    DTPKPacketUnknownReceive *packet, size_t size)
{
    if (size < sizeof(DTPKPacketGenericReceive))
        return false;

    DTPKPacketGenericReceive *generic =
        reinterpret_cast<DTPKPacketGenericReceive *>(packet);

    if (generic->hopLimit <= 1)
    {
        if (generic->type == DATA_SINGLE)
            sendNackPacket(
                generic->originalSender,
                generic->lcmm.mac.sender,
                generic->id,
                generic->finalTarget);
        return false;
    }

    RoutingRecord *routing = _crystDatabase.getRouting(generic->finalTarget);
    if (!routing)
    {
        if (generic->type == DATA_SINGLE)
            sendNackPacket(
                generic->originalSender,
                generic->lcmm.mac.sender,
                generic->id,
                generic->finalTarget);
        return false;
    }

    if (generic->type == DATA_SINGLE &&
        hasSeenData(generic->originalSender, generic->id))
        return false;

    const size_t outgoingSize = size - sizeof(LCMMDataHeader);
    if (outgoingSize < sizeof(DTPKPacketHeader) ||
        outgoingSize > DATASIZE_LCMM)
    {
        if (generic->type == DATA_SINGLE)
            sendNackPacket(
                generic->originalSender,
                generic->lcmm.mac.sender,
                generic->id,
                generic->finalTarget);
        return false;
    }

    DTPKPacketUnknown *forwarded =
        static_cast<DTPKPacketUnknown *>(malloc(outgoingSize));
    if (!forwarded)
        return false;

    memcpy(
        forwarded,
        reinterpret_cast<unsigned char *>(packet) + sizeof(LCMMDataHeader),
        outgoingSize);

    DTPKPacketGeneric *out =
        reinterpret_cast<DTPKPacketGeneric *>(forwarded);
    --out->hopLimit;

    if (generic->type == DATA_SINGLE)
        rememberData(generic->originalSender, generic->id);

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

    DTPKPacketUnknownReceive *dtpk = packet.first;
    if (!dtpk || packet.second < sizeof(DTPKPacketUnknownReceive))
    {
        free(dtpk);
        return;
    }

    noteHeard(dtpk->lcmm.mac.sender);

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
        if (packet.second < sizeof(DTPKPacketGenericReceive))
            break;

        DTPKPacketGenericReceive *generic =
            reinterpret_cast<DTPKPacketGenericReceive *>(dtpk);

        if (generic->finalTarget != MAC::getInstance()->getId())
        {
            forwardRoutedPacket(dtpk, packet.second);
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
                waiting.target == generic->originalSender)
            {
                waiting.gotAck = true;
                waiting.success = success;
            }
        }

        if (_waitingForAck &&
            _currentlySendingId == generic->id)
        {
            _waitingForAck = false;
            _currentlySendingId = 0;
        }
        break;
    }

    default:
        break;
    }

    free(dtpk);
}

std::vector<NeighborRecord> DTPK::getNeighbours()
{
    return _crystDatabase.getListOfNeighbours();
}

void DTPK::controlDeamon()
{
    const uint32_t elapsed = _currentTime - _lastTick;

    _helloRemaining =
        elapsed >= static_cast<uint32_t>(std::max<int32_t>(_helloRemaining, 0))
            ? 0
            : _helloRemaining - static_cast<int32_t>(elapsed);
    if (_helloRemaining <= 0)
    {
        sendHello();
        const long low =
            static_cast<long>(HELLO_PERIOD_MS - HELLO_JITTER_MS);
        const long high =
            static_cast<long>(HELLO_PERIOD_MS + HELLO_JITTER_MS + 1u);
        _helloRemaining = static_cast<int32_t>(random(low, high));
    }

    _maintenanceRemaining =
        elapsed >= static_cast<uint32_t>(
                       std::max<int32_t>(_maintenanceRemaining, 0))
            ? 0
            : _maintenanceRemaining - static_cast<int32_t>(elapsed);
    if (_maintenanceRemaining <= 0)
    {
        expireNeighbours();
        expireAssemblies();
        processSequenceRequests();
        retrySequenceRequests();
        _maintenanceRemaining = static_cast<int32_t>(MAINTENANCE_PERIOD_MS);
    }

    if (_crystTimeout.sendingPacket)
    {
        _crystTimeout.remainingTimeToSend =
            elapsed >= static_cast<uint32_t>(
                           std::max<int32_t>(
                               _crystTimeout.remainingTimeToSend, 0))
                ? 0
                : _crystTimeout.remainingTimeToSend -
                      static_cast<int32_t>(elapsed);

        if (_crystTimeout.remainingTimeToSend <= 0)
        {
            _crystTimeout.sendingPacket = false;
            queueCrystSnapshot();
        }
    }
}

void DTPK::loop()
{
    _currentTime = millis();

    receivingDeamon();
    sendingDeamon();
    timeoutDeamon();
    controlDeamon();

    LCMM::getInstance()->loop();
    _lastTick = _currentTime;
}

uint16_t DTPK::sendPacket(
    uint16_t target,
    unsigned char *payload,
    size_t size,
    int16_t timeout,
    bool dtpkAck,
    PacketAckCallback callback)
{
    RoutingRecord *routing = _crystDatabase.getRouting(target);
    if (!routing)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }

    const size_t wireSize = sizeof(DTPKPacketGeneric) + size;
    if (wireSize > DATASIZE_LCMM)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }

    DTPKPacketGeneric *packet =
        static_cast<DTPKPacketGeneric *>(malloc(wireSize));
    if (!packet)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }

    const uint16_t id = nextPacketId();
    packet->type = DATA_SINGLE;
    packet->id = id;
    packet->originalSender = MAC::getInstance()->getId();
    packet->finalTarget = target;
    packet->flags =
        dtpkAck ? DTPK_FLAG_E2E_ACK_REQUESTED : DTPK_FLAG_NONE;
    packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    if (size > 0)
        memcpy(packet->data, payload, size);

    rememberData(packet->originalSender, id);

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(packet),
        wireSize,
        routing->router,
        timeout,
        0,
        true,
        dtpkAck,
        callback);

    return id;
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint32_t size)
{
    if (!packet || !DTPK::getInstance())
        return;
    DTPK::getInstance()->_packetReceived.push(
        std::make_pair(
            reinterpret_cast<DTPKPacketUnknownReceive *>(packet),
            static_cast<size_t>(size)));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
    (void)id;
    (void)success;
}
