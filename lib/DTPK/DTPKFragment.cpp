#include <DTPK.h>

#include <algorithm>
#include <cstring>
#include <limits>

namespace
{
bool deadlineReached(uint32_t now, uint32_t deadline)
{
    return static_cast<int32_t>(now - deadline) >= 0;
}

bool bitmapBit(const uint8_t *bitmap, uint8_t index)
{
    return (bitmap[index / 8u] & static_cast<uint8_t>(1u << (index % 8u))) != 0;
}

void setBitmapBit(uint8_t *bitmap, uint8_t index)
{
    bitmap[index / 8u] |= static_cast<uint8_t>(1u << (index % 8u));
}
} // namespace

uint8_t DTPK::fragmentCountForSize(size_t size) const
{
    if (size == 0 || size > maximumMessageSize())
        return 0;
    const size_t count =
        (size + fragmentPayloadSize() - 1u) / fragmentPayloadSize();
    return count > MAX_FRAGMENT_COUNT ? 0 : static_cast<uint8_t>(count);
}

size_t DTPK::expectedFragmentBytes(uint16_t totalSize, uint8_t index) const
{
    const uint8_t count = fragmentCountForSize(totalSize);
    if (count == 0 || index >= count)
        return 0;
    const size_t offset = static_cast<size_t>(index) * fragmentPayloadSize();
    return std::min<size_t>(fragmentPayloadSize(), totalSize - offset);
}

uint32_t DTPK::multipartQueryDelayMs()
{
    uint32_t hops = 1;
    if (_multipartSend.active)
    {
        RoutingRecord *route = _crystDatabase.getRouting(_multipartSend.target);
        if (route)
            hops = std::max<uint32_t>(1u, route->distance);
    }
    // A fragment may consume all five LCMM attempts on each relay before the
    // destination can truthfully report it missing. Poll only after that repair
    // window, otherwise duplicate status rounds create redundant fragments.
    const uint64_t delay =
        static_cast<uint64_t>(FRAGMENT_QUERY_INTERVAL_MS) +
        static_cast<uint64_t>(hops) * FRAGMENT_TIMEOUT_PER_PART_MS * 2ull;
    return static_cast<uint32_t>(std::min<uint64_t>(delay, 0x7fffffffu));
}

DTPK::FragmentAssembly *DTPK::findFragmentAssembly(
    uint16_t originalSender,
    uint16_t sourceSequence,
    uint16_t id)
{
    for (FragmentAssembly &assembly : _fragmentAssemblies)
    {
        if (assembly.active &&
            assembly.originalSender == originalSender &&
            assembly.sourceSequence == sourceSequence &&
            assembly.id == id)
            return &assembly;
    }
    return nullptr;
}

void DTPK::resetFragmentAssembly(FragmentAssembly &assembly)
{
    if (assembly.packetBuffer)
        free(assembly.packetBuffer);
    assembly = FragmentAssembly{};
}

DTPK::FragmentAssembly *DTPK::allocateFragmentAssembly(
    uint16_t originalSender,
    uint16_t sourceSequence,
    uint16_t id,
    uint16_t totalSize,
    uint8_t flags,
    uint16_t lastHop)
{
    if (totalSize == 0 || totalSize > maximumMessageSize())
        return nullptr;
    const uint8_t count = fragmentCountForSize(totalSize);
    if (count == 0)
        return nullptr;

    FragmentAssembly *slot = nullptr;
    for (FragmentAssembly &assembly : _fragmentAssemblies)
    {
        if (!assembly.active)
        {
            slot = &assembly;
            break;
        }
        if (!slot ||
            static_cast<uint32_t>(_currentTime - assembly.lastUpdate) >
                static_cast<uint32_t>(_currentTime - slot->lastUpdate))
            slot = &assembly;
    }
    if (!slot)
        return nullptr;
    resetFragmentAssembly(*slot);

    const size_t allocation = sizeof(DTPKPacketGeneric) + totalSize;
    slot->packetBuffer = static_cast<uint8_t *>(malloc(allocation));
    if (!slot->packetBuffer)
        return nullptr;

    slot->active = true;
    slot->id = id;
    slot->sourceSequence = sourceSequence;
    slot->originalSender = originalSender;
    slot->totalSize = totalSize;
    slot->lastHop = lastHop;
    slot->flags = flags;
    slot->fragmentCount = count;
    slot->receivedCount = 0;
    slot->lastUpdate = _currentTime;
    slot->lastStatus = 0;
    slot->received.fill(0);

    DTPKPacketGeneric *application =
        reinterpret_cast<DTPKPacketGeneric *>(slot->packetBuffer);
    application->type = DATA_SINGLE;
    application->id = id;
    application->sourceSequence = sourceSequence;
    application->originalSender = originalSender;
    application->finalTarget = MAC::getInstance()->getId();
    application->flags = flags;
    application->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    return slot;
}

void DTPK::sendFragmentStatus(
    uint16_t originalSender,
    uint16_t sourceSequence,
    uint16_t id,
    uint8_t fragmentCount,
    const uint8_t *received,
    uint16_t lastHop,
    uint16_t queryId)
{
    if (fragmentCount == 0 || !received)
        return;
    const size_t bitmapBytes = (fragmentCount + 7u) / 8u;
    const size_t wireSize = sizeof(DTPKPacketFragmentStatus) + bitmapBytes;
    DTPKPacketFragmentStatus *status =
        static_cast<DTPKPacketFragmentStatus *>(malloc(wireSize));
    if (!status)
        return;

    status->type = FRAGMENT_STATUS;
    status->id = id;
    status->sourceSequence = sourceSequence;
    status->originalSender = MAC::getInstance()->getId();
    status->finalTarget = originalSender;
    status->flags = DTPK_FLAG_NONE;
    status->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    status->fragmentCount = fragmentCount;
    status->queryId = queryId;
    for (size_t byte = 0; byte < bitmapBytes; ++byte)
        status->missing[byte] = static_cast<uint8_t>(~received[byte]);

    const uint8_t remainder = static_cast<uint8_t>(fragmentCount % 8u);
    if (remainder != 0)
    {
        const uint8_t validMask = static_cast<uint8_t>((1u << remainder) - 1u);
        status->missing[bitmapBytes - 1u] &= validMask;
    }

    uint16_t nextHop = lastHop;
    if (nextHop == 0)
    {
        RoutingRecord *route = _crystDatabase.getRouting(originalSender);
        if (!route)
        {
            free(status);
            return;
        }
        nextHop = route->router;
    }

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(status),
        wireSize,
        nextHop,
        5000,
        0,
        true,
        false,
        nullptr,
        true);
}

void DTPK::maintainFragmentAssemblies()
{
    // Do not emit unsolicited missing bitmaps during the initial stream: a slow
    // relay may simply not have received the later fragment indices yet. The
    // final fragment triggers an immediate status, and an explicit source query
    // repairs a lost final fragment or lost status without false retransmits.
    for (FragmentAssembly &assembly : _fragmentAssemblies)
    {
        if (!assembly.active)
            continue;
        if (static_cast<uint32_t>(_currentTime - assembly.lastUpdate) >=
            FRAGMENT_ASSEMBLY_EXPIRY_MS)
            resetFragmentAssembly(assembly);
    }
}

bool DTPK::queueMultipartFragment(uint8_t index)
{
    if (!_multipartSend.active || index >= _multipartSend.fragmentCount)
        return false;
    RoutingRecord *route = _crystDatabase.getRouting(_multipartSend.target);
    if (!route)
        return false;

    const size_t payloadBytes =
        expectedFragmentBytes(_multipartSend.totalSize, index);
    if (payloadBytes == 0)
        return false;
    const size_t wireSize = sizeof(DTPKPacketFragment) + payloadBytes;
    DTPKPacketFragment *fragment =
        static_cast<DTPKPacketFragment *>(malloc(wireSize));
    if (!fragment)
        return false;

    fragment->type = DATA_FRAGMENT;
    fragment->id = _multipartSend.id;
    fragment->sourceSequence = _multipartSend.sourceSequence;
    fragment->originalSender = MAC::getInstance()->getId();
    fragment->finalTarget = _multipartSend.target;
    fragment->flags = DTPK_FLAG_E2E_ACK_REQUESTED;
    fragment->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    fragment->totalSize = _multipartSend.totalSize;
    fragment->fragmentIndex = index;
    const size_t offset = static_cast<size_t>(index) * fragmentPayloadSize();
    memcpy(fragment->data, _multipartSend.payload + offset, payloadBytes);

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(fragment),
        wireSize,
        route->router,
        10000,
        0,
        true,
        false,
        nullptr,
        false);
    return true;
}

bool DTPK::queueFragmentQuery()
{
    if (!_multipartSend.active)
        return false;
    RoutingRecord *route = _crystDatabase.getRouting(_multipartSend.target);
    if (!route)
        return false;

    DTPKPacketFragmentQuery *query =
        static_cast<DTPKPacketFragmentQuery *>(
            malloc(sizeof(DTPKPacketFragmentQuery)));
    if (!query)
        return false;
    query->type = FRAGMENT_QUERY;
    query->id = _multipartSend.id;
    query->sourceSequence = _multipartSend.sourceSequence;
    query->originalSender = MAC::getInstance()->getId();
    query->finalTarget = _multipartSend.target;
    query->flags = DTPK_FLAG_E2E_ACK_REQUESTED;
    query->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
    query->totalSize = _multipartSend.totalSize;
    query->queryId = _multipartSend.nextQueryId;
    ++_multipartSend.nextQueryId;
    if (_multipartSend.nextQueryId == 0)
        ++_multipartSend.nextQueryId;

    addPacketToSendingQueue(
        reinterpret_cast<DTPKPacketUnknown *>(query),
        sizeof(DTPKPacketFragmentQuery),
        route->router,
        5000,
        0,
        true,
        false,
        nullptr,
        true);
    return true;
}

void DTPK::pumpMultipartSend()
{
    if (!_multipartSend.active)
        return;

    for (const DTPKPacketRequest &request : _packetRequests)
    {
        if (!request.packet || request.packet->id != _multipartSend.id)
            continue;
        if (request.packet->type == DATA_FRAGMENT ||
            request.packet->type == FRAGMENT_QUERY)
            return; // keep at most one source fragment/query allocated
    }

    if (_multipartSend.nextInitialFragment < _multipartSend.fragmentCount)
    {
        const uint8_t index = _multipartSend.nextInitialFragment;
        if (queueMultipartFragment(index))
        {
            ++_multipartSend.nextInitialFragment;
        }
        return;
    }

    for (uint16_t index = 0; index < _multipartSend.fragmentCount; ++index)
    {
        if (_multipartSend.retransmit[index] == 0)
            continue;
        if (queueMultipartFragment(static_cast<uint8_t>(index)))
            _multipartSend.retransmit[index] = 0;
        return;
    }

    if (deadlineReached(_currentTime, _multipartSend.nextQueryAt))
        (void)queueFragmentQuery();
}

void DTPK::clearMultipartSend()
{
    if (!_multipartSend.active)
        return;
    const uint16_t id = _multipartSend.id;
    const uint16_t sequence = _multipartSend.sourceSequence;

    auto end = std::remove_if(
        _packetRequests.begin(),
        _packetRequests.end(),
        [id, sequence](DTPKPacketRequest &request) {
            if (!request.packet || request.packet->id != id)
                return false;
            if (request.packet->type != DATA_FRAGMENT &&
                request.packet->type != FRAGMENT_QUERY)
                return false;
            const DTPKPacketGeneric *generic =
                reinterpret_cast<const DTPKPacketGeneric *>(request.packet);
            if (generic->sourceSequence != sequence)
                return false;
            free(request.packet);
            request.packet = nullptr;
            return true;
        });
    _packetRequests.erase(end, _packetRequests.end());

    _multipartLcmmIds.clear();
    if (_multipartSend.payload)
        free(_multipartSend.payload);
    _multipartSend = MultipartSend{};
}

void DTPK::parseFragmentPacket(const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketFragment))
        return;
    DTPKPacketFragment *fragment =
        reinterpret_cast<DTPKPacketFragment *>(packet.frame->data);
    const uint8_t count = fragmentCountForSize(fragment->totalSize);
    if (count == 0 || fragment->fragmentIndex >= count)
        return;
    const size_t expected =
        expectedFragmentBytes(fragment->totalSize, fragment->fragmentIndex);
    if (packet.dtpkSize != sizeof(DTPKPacketFragment) + expected)
        return;

    const uint16_t lastHop = packet.frame->mac.sender;
    if (hasSeenData(
            fragment->originalSender,
            fragment->sourceSequence,
            fragment->id))
    {
        sendAckPacket(
            fragment->originalSender,
            lastHop,
            fragment->id,
            fragment->sourceSequence);
        return;
    }

    FragmentAssembly *assembly = findFragmentAssembly(
        fragment->originalSender,
        fragment->sourceSequence,
        fragment->id);
    if (!assembly)
    {
        assembly = allocateFragmentAssembly(
            fragment->originalSender,
            fragment->sourceSequence,
            fragment->id,
            fragment->totalSize,
            fragment->flags,
            lastHop);
    }
    if (!assembly || assembly->totalSize != fragment->totalSize ||
        assembly->fragmentCount != count)
        return;

    assembly->lastHop = lastHop;
    assembly->lastUpdate = _currentTime;
    assembly->flags |= fragment->flags;
    if (!bitmapBit(assembly->received.data(), fragment->fragmentIndex))
    {
        DTPKPacketGeneric *application =
            reinterpret_cast<DTPKPacketGeneric *>(assembly->packetBuffer);
        const size_t offset =
            static_cast<size_t>(fragment->fragmentIndex) * fragmentPayloadSize();
        memcpy(application->data + offset, fragment->data, expected);
        setBitmapBit(assembly->received.data(), fragment->fragmentIndex);
        ++assembly->receivedCount;
    }

    if (assembly->receivedCount == assembly->fragmentCount)
    {
        DTPKPacketGeneric *application =
            reinterpret_cast<DTPKPacketGeneric *>(assembly->packetBuffer);
        rememberData(
            assembly->originalSender,
            assembly->sourceSequence,
            assembly->id);
        if (_recieveCallback)
            _recieveCallback(
                application,
                static_cast<uint16_t>(
                    sizeof(DTPKPacketGeneric) + assembly->totalSize));
        if ((assembly->flags & DTPK_FLAG_E2E_ACK_REQUESTED) != 0)
            sendAckPacket(
                assembly->originalSender,
                assembly->lastHop,
                assembly->id,
                assembly->sourceSequence);
        resetFragmentAssembly(*assembly);
        return;
    }

    if (fragment->fragmentIndex + 1u == count)
    {
        sendFragmentStatus(
            assembly->originalSender,
            assembly->sourceSequence,
            assembly->id,
            assembly->fragmentCount,
            assembly->received.data(),
            assembly->lastHop,
            0);
        assembly->lastStatus = _currentTime;
    }
}

void DTPK::parseFragmentQueryPacket(const ReceivedPacket &packet)
{
    if (packet.dtpkSize != sizeof(DTPKPacketFragmentQuery))
        return;
    DTPKPacketFragmentQuery *query =
        reinterpret_cast<DTPKPacketFragmentQuery *>(packet.frame->data);
    const uint8_t count = fragmentCountForSize(query->totalSize);
    if (count == 0)
        return;
    const uint16_t lastHop = packet.frame->mac.sender;

    if (hasSeenData(query->originalSender, query->sourceSequence, query->id))
    {
        sendAckPacket(
            query->originalSender,
            lastHop,
            query->id,
            query->sourceSequence);
        return;
    }

    FragmentAssembly *assembly = findFragmentAssembly(
        query->originalSender,
        query->sourceSequence,
        query->id);
    if (assembly && assembly->totalSize == query->totalSize)
    {
        assembly->lastHop = lastHop;
        assembly->lastUpdate = _currentTime;
        sendFragmentStatus(
            assembly->originalSender,
            assembly->sourceSequence,
            assembly->id,
            assembly->fragmentCount,
            assembly->received.data(),
            lastHop,
            query->queryId);
        assembly->lastStatus = _currentTime;
        return;
    }

    std::array<uint8_t, FRAGMENT_BITMAP_BYTES> received{};
    sendFragmentStatus(
        query->originalSender,
        query->sourceSequence,
        query->id,
        count,
        received.data(),
        lastHop,
        query->queryId);
}

void DTPK::parseFragmentStatusPacket(const ReceivedPacket &packet)
{
    if (packet.dtpkSize < sizeof(DTPKPacketFragmentStatus))
        return;
    DTPKPacketFragmentStatus *status =
        reinterpret_cast<DTPKPacketFragmentStatus *>(packet.frame->data);
    if (!_multipartSend.active ||
        status->id != _multipartSend.id ||
        status->sourceSequence != _multipartSend.sourceSequence ||
        status->originalSender != _multipartSend.target ||
        status->fragmentCount != _multipartSend.fragmentCount)
        return;

    const size_t bitmapBytes = (status->fragmentCount + 7u) / 8u;
    if (packet.dtpkSize != sizeof(DTPKPacketFragmentStatus) + bitmapBytes)
        return;
    if (status->queryId == _multipartSend.lastStatusQueryId)
        return;
    _multipartSend.lastStatusQueryId = status->queryId;

    bool missing = false;
    for (uint16_t index = 0; index < status->fragmentCount; ++index)
    {
        if (!bitmapBit(status->missing, static_cast<uint8_t>(index)))
            continue;
        _multipartSend.retransmit[index] = 1;
        missing = true;
    }
    if (missing)
        pumpMultipartSend();
}
