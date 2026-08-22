#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

DTPK *DTPK::dtpk = nullptr;

void DTPK::initialize(uint8_t KLimit, uint16_t originSequence, bool mobileHint)
{
    if (!dtpk)
        dtpk = new DTPK(KLimit, originSequence, mobileHint);
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

uint32_t DTPK::effectiveHelloPeriodMs() const
{
    uint32_t period = _mobileHint ? MOBILE_HELLO_PERIOD_MS : HELLO_PERIOD_MS;
    const uint8_t duty = MAC::getInstance()->getFallbackDutyCyclePercent();
    if (duty > 0 && duty <= 1)
        period = std::max<uint32_t>(period, 60000u);
    return period;
}

DTPK::DTPK(uint8_t KLimit, uint16_t originSequence, bool mobileHint)
    : _crystDatabase(MAC::getInstance()->getId())
{
    _Klimit = KLimit;
    _packetCounter = 0;
    _timeOfInit = millis();
    _currentTime = _timeOfInit;
    _lastTick = _currentTime;
    _mobileHint = mobileHint;

    _seed = MathExtension.murmur64(
        (static_cast<uint64_t>(MAC::getInstance()->random()) << 32) |
        MAC::getInstance()->random());
    randomSeed(_seed);

    _originSequence = originSequence == 0 ? 1 : originSequence;
    _routeVersion = 1;
    const uint32_t helloPeriod = effectiveHelloPeriodMs();
    _helloRemaining = static_cast<int32_t>(
        random(100, static_cast<long>(helloPeriod + 1u)));
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
        for (auto it = _livenessProbeByLcmmId.begin();
             it != _livenessProbeByLcmmId.end();)
        {
            if (it->second == neighbor)
                it = _livenessProbeByLcmmId.erase(it);
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

        if (type == CRYST_REQ && request.lcmmAck &&
            request.target != BROADCAST)
            _livenessProbeByLcmmId[lcmmId] = request.target;

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
