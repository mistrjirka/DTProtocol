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
        const uint32_t helloJitter = helloPeriod / 5u; // ±20%
        const long low = static_cast<long>(helloPeriod - helloJitter);
        const long high = static_cast<long>(helloPeriod + helloJitter + 1u);
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

    auto probe = self->_livenessProbeByLcmmId.find(id);
    if (probe == self->_livenessProbeByLcmmId.end())
        return;

    const uint16_t neighbor = probe->second;
    self->_livenessProbeByLcmmId.erase(probe);
    self->_currentTime = millis();

    if (success)
        self->noteHeard(neighbor);

    // A failed probe is only absence of evidence on a lossy half-duplex RF
    // link. It must not become authoritative evidence that the neighbour is
    // gone; hard inactivity expiry is the topology-changing criterion.
}
