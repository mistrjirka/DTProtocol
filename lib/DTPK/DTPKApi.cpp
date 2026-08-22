#include "mathextension.h"
#include <DTPK.h>

#include <algorithm>
#include <cstring>

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
        const uint32_t helloPeriod = effectiveHelloPeriodMs();
        const uint32_t helloJitter = helloPeriod / 5u;
        const long low = static_cast<long>(helloPeriod - helloJitter);
        // Mobile nodes keep random phase but never exceed their advertised
        // maximum gap. Static nodes retain the previous symmetric ±20% jitter.
        const long high = static_cast<long>(
            helloPeriod + (_mobileHint ? 0u : helloJitter) + 1u);
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
        maintainFragmentAssemblies();
        _maintenanceRemaining = static_cast<int32_t>(MAINTENANCE_PERIOD_MS);
    }

    if (_crystRemaining >= 0)
    {
        _crystRemaining =
            elapsed >= static_cast<uint32_t>(_crystRemaining)
                ? 0
                : _crystRemaining - static_cast<int32_t>(elapsed);

        if (_crystRemaining == 0)
        {
            _crystRemaining = -1;
            queueCrystSnapshot();
        }
    }
}

void DTPK::loop()
{
    // Service the radio/link layer first. A completed LCMM link ACK can release
    // a received DTPK frame, which receivingDeamon() then processes in this same
    // firmware turn before any unrelated queued transmission is started.
    LCMM::getInstance()->loop();
    _currentTime = millis();

    receivingDeamon();
    pumpMultipartSend();
    sendingDeamon();
    timeoutDeamon();
    controlDeamon();

    _lastTick = _currentTime;
}

uint16_t DTPK::sendPacket(
    uint16_t target,
    unsigned char *payload,
    size_t size,
    int32_t timeout,
    bool dtpkAck,
    PacketAckCallback callback)
{
    RoutingRecord *routing = _crystDatabase.getRouting(target);
    if (!routing || (size > 0 && !payload))
    {
        if (callback)
            callback(0, 0);
        return 0;
    }

    if (size <= maximumSinglePayloadSize())
    {
        const size_t wireSize = sizeof(DTPKPacketGeneric) + size;
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
        packet->sourceSequence = _originSequence;
        packet->originalSender = MAC::getInstance()->getId();
        packet->finalTarget = target;
        packet->flags =
            dtpkAck ? DTPK_FLAG_E2E_ACK_REQUESTED : DTPK_FLAG_NONE;
        packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
        if (size > 0)
            memcpy(packet->data, payload, size);

        rememberData(packet->originalSender, packet->sourceSequence, id);
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

    // Keep one source-side multipart message in flight. This bounds RAM and
    // makes selective retransmission deterministic while ordinary small packets
    // may remain queued behind the existing E2E gate.
    if (size > maximumMessageSize() || _multipartSend.active)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }
    const uint8_t count = fragmentCountForSize(size);
    if (count == 0 || size > UINT16_MAX)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }

    uint8_t *copy = static_cast<uint8_t *>(malloc(size));
    if (!copy)
    {
        if (callback)
            callback(0, 0);
        return 0;
    }
    memcpy(copy, payload, size);

    const uint16_t id = nextPacketId();
    _multipartSend.active = true;
    _multipartSend.id = id;
    _multipartSend.sourceSequence = _originSequence;
    _multipartSend.target = target;
    _multipartSend.totalSize = static_cast<uint16_t>(size);
    _multipartSend.fragmentCount = count;
    _multipartSend.nextInitialFragment = 0;
    _multipartSend.nextQueryAt = _currentTime + FRAGMENT_QUERY_INTERVAL_MS;
    _multipartSend.nextQueryId = 1;
    _multipartSend.lastStatusQueryId = UINT16_MAX;
    _multipartSend.payload = copy;
    _multipartSend.retransmit.fill(0);

    const uint64_t routeHops = std::max<uint64_t>(1u, routing->distance);
    const uint64_t minimumTimeout =
        15000ull +
        (static_cast<uint64_t>(count) + 1ull) * routeHops *
            FRAGMENT_TIMEOUT_PER_PART_MS;
    const uint32_t requested = timeout > 0 ? static_cast<uint32_t>(timeout) : 1u;
    const uint32_t effectiveTimeout = static_cast<uint32_t>(std::min<uint64_t>(
        std::max<uint64_t>(requested, minimumTimeout),
        static_cast<uint64_t>(0x7fffffffu)));

    DTPKPacketWaiting waiting{};
    waiting.id = id;
    waiting.sourceSequence = _originSequence;
    waiting.target = target;
    waiting.timeLeft = static_cast<int32_t>(effectiveTimeout);
    waiting.timeout = effectiveTimeout;
    waiting.gotAck = false;
    waiting.success = false;
    // Internal ACK/status is always required for selective repair, but preserve
    // the public no-E2E-ACK behavior by suppressing a success callback when the
    // caller did not request one.
    waiting.callback = dtpkAck ? callback : nullptr;
    _packetWaiting.push_back(waiting);
    pumpMultipartSend();
    return id;
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint32_t size)
{
    DTPK *self = DTPK::getInstance();
    if (!packet || !self)
    {
        free(packet);
        return;
    }
    if (size < sizeof(LCMMDataHeader) + sizeof(DTPKPacketUnknown))
    {
        free(packet);
        return;
    }

    self->_packetReceived.push(
        ReceivedPacket{
            packet,
            static_cast<size_t>(size - sizeof(LCMMDataHeader))});
}

void DTPK::receiveAck(uint16_t id, bool success)
{
    DTPK *self = DTPK::getInstance();
    if (!self)
        return;

    self->_currentTime = millis();
    auto multipart = self->_multipartLcmmIds.find(id);
    if (multipart != self->_multipartLcmmIds.end())
    {
        self->_multipartLcmmIds.erase(multipart);
        if (self->_multipartSend.active)
            self->_multipartSend.nextQueryAt =
                self->_currentTime + self->multipartQueryDelayMs();
    }

    auto evidence = self->_directAckNeighborByLcmmId.find(id);
    if (evidence == self->_directAckNeighborByLcmmId.end())
        return;

    const uint16_t neighbor = evidence->second;
    self->_directAckNeighborByLcmmId.erase(evidence);
    if (success)
        self->noteHeard(neighbor);

    // A failed reliable exchange is only absence of evidence on a lossy
    // half-duplex RF link. Hard inactivity remains topology-authoritative.
}
