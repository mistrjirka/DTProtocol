#include <bluetooth.h>

#if DTPK_BLE_AVAILABLE

#include <algorithm>
#include <cstring>
#include <utility>

Bluetooth *Bluetooth::instance = nullptr;

class DTPKBLEServerCallbacks : public BLEServerCallbacks
{
    void onConnect(BLEServer *) override
    {
        Bluetooth::getInstance()->handleConnection(true);
    }

    void onDisconnect(BLEServer *) override
    {
        Bluetooth::getInstance()->handleConnection(false);
    }
};

class DTPKBLEMessageCallbacks : public BLECharacteristicCallbacks
{
    void onWrite(BLECharacteristic *characteristic) override
    {
        Bluetooth::getInstance()->handleWrite(characteristic->getValue());
    }
};

Bluetooth::Bluetooth()
    : callbackMutex(xSemaphoreCreateMutex())
{
}

Bluetooth *Bluetooth::getInstance()
{
    if (!instance)
        instance = new Bluetooth();
    return instance;
}

void Bluetooth::initialize()
{
    (void)getInstance();
}

uint16_t Bluetooth::nextMessageId()
{
    ++messageCounter;
    if (messageCounter == 0)
        ++messageCounter;
    return messageCounter;
}

BluetoothDiagnostics Bluetooth::getDiagnostics() const
{
    BluetoothDiagnostics diagnostics;
    diagnostics.droppedWrites =
        droppedWrites.load(std::memory_order_relaxed);
    diagnostics.droppedInboundMessages =
        droppedInboundMessages.load(std::memory_order_relaxed);
    diagnostics.droppedControlNotifications =
        droppedControlNotifications.load(std::memory_order_relaxed);
    return diagnostics;
}

uint16_t Bluetooth::negotiatedMtu() const
{
    if (!deviceConnected || !server)
        return BLE_DEFAULT_ATT_MTU;
    const uint16_t peerMtu = server->getPeerMTU(server->getConnId());
    return peerMtu >= BLE_DEFAULT_ATT_MTU ? peerMtu : BLE_DEFAULT_ATT_MTU;
}

size_t Bluetooth::notificationCapacity() const
{
    return bleNotificationBytesForMtu(negotiatedMtu());
}

bool Bluetooth::setup()
{
    if (ready)
        return true;
    if (!callbackMutex || !DTPK::getInstance())
        return false;

    BLEDevice::init(deviceName);
    BLEDevice::setMTU(BLE_REQUESTED_ATT_MTU);
    server = BLEDevice::createServer();
    if (!server)
        return false;
    server->setCallbacks(new DTPKBLEServerCallbacks());

    BLEService *service = server->createService(WATCH_SERVICE_UUID);
    if (!service)
        return false;

    messageCharacteristic = service->createCharacteristic(
        MSG_CHAR_UUID,
        BLECharacteristic::PROPERTY_READ |
            BLECharacteristic::PROPERTY_WRITE |
            BLECharacteristic::PROPERTY_WRITE_NR |
            BLECharacteristic::PROPERTY_NOTIFY);
    neighborCountCharacteristic = service->createCharacteristic(
        NEIGHCOUNT_CHAR_UUID,
        BLECharacteristic::PROPERTY_READ |
            BLECharacteristic::PROPERTY_NOTIFY);
    if (!messageCharacteristic || !neighborCountCharacteristic)
        return false;

    messageCharacteristic->addDescriptor(new BLE2902());
    neighborCountCharacteristic->addDescriptor(new BLE2902());
    messageCharacteristic->setCallbacks(new DTPKBLEMessageCallbacks());

    uint16_t zero = 0;
    neighborCountCharacteristic->setValue(
        reinterpret_cast<uint8_t *>(&zero), sizeof(zero));

    service->start();
    DTPK::getInstance()->setPacketReceivedCallback(
        [this](DTPKPacketGeneric *packet, uint16_t size) {
            handleDTPKPacket(packet, size);
        });

    ready = true;
    startAdvertising();
    return true;
}

void Bluetooth::startAdvertising()
{
    if (!ready)
        return;
    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    if (!advertising)
        return;
    advertising->addServiceUUID(WATCH_SERVICE_UUID);
    advertising->setScanResponse(true);
    advertising->setMinPreferred(0x06);
    advertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
}

void Bluetooth::stopAdvertising()
{
    if (ready)
        BLEDevice::stopAdvertising();
}

void Bluetooth::handleConnection(bool connected)
{
    desiredConnected.store(connected, std::memory_order_relaxed);
    connectionStateDirty.store(true, std::memory_order_release);
}

void Bluetooth::clearCallbackQueues()
{
    if (!callbackMutex ||
        xSemaphoreTake(callbackMutex, 0) != pdTRUE)
        return;
    pendingWrites.clear();
    pendingWriteErrors.clear();
    xSemaphoreGive(callbackMutex);
}

void Bluetooth::clearLoopOwnedQueues()
{
    controlNotifications.clear();
    inboundNotifications.clear();
    pendingNeighbors = PendingNeighbors{};
}

void Bluetooth::applyConnectionState()
{
    if (!connectionStateDirty.exchange(false, std::memory_order_acq_rel))
        return;

    deviceConnected = desiredConnected.load(std::memory_order_relaxed);
    if (deviceConnected)
    {
        advertisingRestartPending = false;
        neighborsUpdateRequested.store(true, std::memory_order_release);
        return;
    }

    disconnectionTime = millis();
    advertisingRestartPending = true;
    clearCallbackQueues();
    clearLoopOwnedQueues();
}

void Bluetooth::loop()
{
    applyConnectionState();

    if (advertisingRestartPending &&
        static_cast<uint32_t>(millis() - disconnectionTime) >= 250u)
    {
        startAdvertising();
        advertisingRestartPending = false;
    }

    processPendingWrite();

    DTPK *dtpk = DTPK::getInstance();
    if (dtpk)
        dtpk->loop();

    periodicNeighborUpdate();
    prepareNeighborsUpdate();
    pumpMessageNotification();
}

bool Bluetooth::notificationsEnabled() const
{
    if (!messageCharacteristic)
        return false;
    BLEDescriptor *descriptor =
        messageCharacteristic->getDescriptorByUUID(static_cast<uint16_t>(0x2902));
    if (!descriptor)
        return true;
    return static_cast<BLE2902 *>(descriptor)->getNotifications();
}

bool Bluetooth::notify(
    BLECharacteristic *characteristic,
    const uint8_t *bytes,
    size_t size)
{
    if (!ready || !deviceConnected || !characteristic || !bytes ||
        size == 0 || size > notificationCapacity() || !notificationsEnabled())
        return false;
    characteristic->setValue(const_cast<uint8_t *>(bytes), size);
    characteristic->notify();
    return true;
}

bool Bluetooth::queueControlNotification(
    std::vector<uint8_t> bytes, bool priority)
{
    if (!deviceConnected || bytes.empty())
        return false;
    if (controlNotifications.size() >= MAX_CONTROL_NOTIFICATIONS)
    {
        if (!priority)
        {
            droppedControlNotifications.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        controlNotifications.pop_back();
        droppedControlNotifications.fetch_add(1, std::memory_order_relaxed);
    }
    if (priority)
        controlNotifications.push_front(std::move(bytes));
    else
        controlNotifications.push_back(std::move(bytes));
    return true;
}

void Bluetooth::handleWrite(const std::string &value)
{
    uint16_t messageId = 0;
    if (value.size() >= sizeof(BLEMessageHeader))
    {
        BLEMessageHeader header{};
        memcpy(&header, value.data(), sizeof(header));
        messageId = header.messageId;
    }

    const size_t maximumWireSize =
        sizeof(BLEOutboundMessage) + DTPK::maximumMessageSize();
    if (!callbackMutex ||
        xSemaphoreTake(callbackMutex, 0) != pdTRUE)
    {
        droppedWrites.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    if (value.size() > maximumWireSize ||
        pendingWrites.size() >= MAX_PENDING_WRITES)
    {
        if (pendingWriteErrors.size() < MAX_PENDING_WRITE_ERRORS)
            pendingWriteErrors.push_back(messageId);
        droppedWrites.fetch_add(1, std::memory_order_relaxed);
        xSemaphoreGive(callbackMutex);
        return;
    }

    pendingWrites.emplace_back(value.begin(), value.end());
    xSemaphoreGive(callbackMutex);
}

void Bluetooth::processPendingWrite()
{
    std::vector<uint8_t> bytes;
    uint16_t rejectedId = 0;
    bool rejected = false;

    if (callbackMutex &&
        xSemaphoreTake(callbackMutex, pdMS_TO_TICKS(1)) == pdTRUE)
    {
        if (!pendingWriteErrors.empty())
        {
            rejectedId = pendingWriteErrors.front();
            pendingWriteErrors.pop_front();
            rejected = true;
        }
        else if (!pendingWrites.empty())
        {
            bytes = std::move(pendingWrites.front());
            pendingWrites.pop_front();
        }
        xSemaphoreGive(callbackMutex);
    }

    if (rejected)
    {
        sendAckMessage(rejectedId, false, 0);
        return;
    }
    if (bytes.empty())
        return;

    BLEOutboundView view;
    if (!parseBLEOutboundMessage(
            bytes.data(), bytes.size(), DTPK::maximumMessageSize(), view))
    {
        uint16_t messageId = 0;
        if (bytes.size() >= sizeof(BLEMessageHeader))
        {
            BLEMessageHeader header{};
            memcpy(&header, bytes.data(), sizeof(header));
            messageId = header.messageId;
        }
        sendAckMessage(messageId, false, 0);
        return;
    }

    DTPK *dtpk = DTPK::getInstance();
    if (!dtpk)
    {
        sendAckMessage(view.header.messageId, false, 0);
        return;
    }

    const uint16_t phoneMessageId = view.header.messageId;
    (void)dtpk->sendPacket(
        view.recipientId,
        const_cast<unsigned char *>(view.payload),
        view.payloadSize,
        60000,
        true,
        [phoneMessageId](uint8_t result, uint16_t ping) {
            Bluetooth::getInstance()->sendAckMessage(
                phoneMessageId, result != 0, ping);
        });
}

void Bluetooth::handleDTPKPacket(DTPKPacketGeneric *packet, uint16_t size)
{
    if (!packet || size < sizeof(DTPKPacketGeneric))
        return;

    const size_t payloadSize = size - sizeof(DTPKPacketGeneric);
    sendInboundMessage(packet->originalSender, packet->data, payloadSize);
    if (dtpkCallback)
        dtpkCallback(packet, size);
}

void Bluetooth::periodicNeighborUpdate()
{
    static uint32_t lastUpdate = 0;
    if (deviceConnected &&
        static_cast<uint32_t>(millis() - lastUpdate) >= 5000u)
    {
        sendNeighborsUpdate();
        lastUpdate = millis();
    }
}

void Bluetooth::sendAckMessage(
    uint16_t originalMessageId,
    bool success,
    uint16_t ping)
{
    std::vector<uint8_t> buffer(sizeof(BLEAckMessage));
    BLEAckMessage *message =
        reinterpret_cast<BLEAckMessage *>(buffer.data());
    message->header.type = BLE_MSG_TYPE_ACK;
    message->header.messageId = nextMessageId();
    message->header.length = sizeof(BLEAckMessage);
    message->originalMessageId = originalMessageId;
    message->success = success ? 1u : 0u;
    message->ping = ping;
    queueControlNotification(std::move(buffer), true);
}

void Bluetooth::sendInboundMessage(
    uint16_t senderId,
    const uint8_t *payload,
    size_t payloadSize)
{
    if (payloadSize > UINT16_MAX || (payloadSize > 0 && !payload))
        return;
    if (inboundNotifications.size() >= MAX_PENDING_INBOUND)
    {
        droppedInboundMessages.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    PendingInbound pending;
    pending.messageId = nextMessageId();
    pending.senderId = senderId;
    if (payloadSize)
        pending.payload.assign(payload, payload + payloadSize);
    inboundNotifications.push_back(std::move(pending));
}

void Bluetooth::sendNeighborsUpdate()
{
    neighborsUpdateRequested.store(true, std::memory_order_release);
}

void Bluetooth::prepareNeighborsUpdate()
{
    if (!deviceConnected ||
        !neighborsUpdateRequested.exchange(false, std::memory_order_acq_rel))
        return;

    DTPK *dtpk = DTPK::getInstance();
    if (!dtpk)
        return;

    pendingNeighbors.routes = dtpk->getNeighbours();
    pendingNeighbors.offset = 0;
    pendingNeighbors.emptyPagePending = pendingNeighbors.routes.empty();
    pendingNeighbors.active = true;

    const uint16_t fullCount = static_cast<uint16_t>(
        std::min<size_t>(pendingNeighbors.routes.size(), UINT16_MAX));
    if (neighborCountCharacteristic)
    {
        neighborCountCharacteristic->setValue(
            const_cast<uint8_t *>(
                reinterpret_cast<const uint8_t *>(&fullCount)),
            sizeof(fullCount));
        BLEDescriptor *descriptor = neighborCountCharacteristic->getDescriptorByUUID(
            static_cast<uint16_t>(0x2902));
        if (!descriptor ||
            static_cast<BLE2902 *>(descriptor)->getNotifications())
            neighborCountCharacteristic->notify();
    }
}

bool Bluetooth::pumpControlNotification()
{
    if (controlNotifications.empty())
        return false;
    std::vector<uint8_t> &bytes = controlNotifications.front();
    if (bytes.size() > notificationCapacity())
    {
        controlNotifications.pop_front();
        droppedControlNotifications.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    if (!notify(messageCharacteristic, bytes.data(), bytes.size()))
        return false;
    controlNotifications.pop_front();
    return true;
}

bool Bluetooth::pumpInboundNotification()
{
    if (inboundNotifications.empty())
        return false;

    PendingInbound &pending = inboundNotifications.front();
    const uint16_t mtu = negotiatedMtu();
    const size_t capacity = notificationCapacity();
    if (pending.offset == 0 &&
        pending.payload.size() <= maxBLEInboundPayloadForMtu(mtu))
    {
        std::vector<uint8_t> buffer(
            sizeof(BLEInboundMessage) + pending.payload.size());
        BLEInboundMessage *message =
            reinterpret_cast<BLEInboundMessage *>(buffer.data());
        message->header.type = BLE_MSG_TYPE_INBOUND;
        message->header.messageId = pending.messageId;
        message->header.length = static_cast<uint16_t>(buffer.size());
        message->senderId = pending.senderId;
        if (!pending.payload.empty())
            memcpy(message->data, pending.payload.data(), pending.payload.size());
        if (!notify(messageCharacteristic, buffer.data(), buffer.size()))
            return false;
        inboundNotifications.pop_front();
        return true;
    }

    const size_t chunkCapacity = maxBLEInboundFragmentPayloadForMtu(mtu);
    if (chunkCapacity == 0 || capacity < sizeof(BLEInboundFragmentMessage))
    {
        inboundNotifications.pop_front();
        droppedInboundMessages.fetch_add(1, std::memory_order_relaxed);
        return false;
    }

    const size_t chunk = std::min(
        chunkCapacity, pending.payload.size() - pending.offset);
    std::vector<uint8_t> buffer(
        sizeof(BLEInboundFragmentMessage) + chunk);
    BLEInboundFragmentMessage *message =
        reinterpret_cast<BLEInboundFragmentMessage *>(buffer.data());
    message->header.type = BLE_MSG_TYPE_INBOUND_FRAGMENT;
    message->header.messageId = pending.messageId;
    message->header.length = static_cast<uint16_t>(buffer.size());
    message->senderId = pending.senderId;
    message->totalLength = static_cast<uint16_t>(pending.payload.size());
    message->offset = static_cast<uint16_t>(pending.offset);
    if (chunk)
        memcpy(message->data, pending.payload.data() + pending.offset, chunk);

    if (!notify(messageCharacteristic, buffer.data(), buffer.size()))
        return false;
    pending.offset += chunk;
    if (pending.offset >= pending.payload.size())
        inboundNotifications.pop_front();
    return true;
}

bool Bluetooth::pumpNeighborNotification()
{
    if (!pendingNeighbors.active)
        return false;

    const uint16_t mtu = negotiatedMtu();
    const size_t capacity = notificationCapacity();
    const size_t pageCapacity = maxBLENeighborsPerNotificationForMtu(mtu);
    if (capacity < sizeof(BLENeighborsMessage) || pageCapacity == 0)
        return false;

    const size_t remaining =
        pendingNeighbors.routes.size() - pendingNeighbors.offset;
    const size_t count = std::min(pageCapacity, remaining);
    if (count == 0 && !pendingNeighbors.emptyPagePending)
    {
        pendingNeighbors = PendingNeighbors{};
        return false;
    }

    std::vector<uint8_t> buffer(
        sizeof(BLENeighborsMessage) + count * sizeof(BLENeighborInfo));
    BLENeighborsMessage *message =
        reinterpret_cast<BLENeighborsMessage *>(buffer.data());
    message->header.type = BLE_MSG_TYPE_NEIGHBORS;
    message->header.messageId = nextMessageId();
    message->header.length = static_cast<uint16_t>(buffer.size());
    message->totalCount = static_cast<uint16_t>(
        std::min<size_t>(pendingNeighbors.routes.size(), UINT16_MAX));
    message->offset = static_cast<uint16_t>(pendingNeighbors.offset);
    message->count = static_cast<uint8_t>(count);

    for (size_t index = 0; index < count; ++index)
    {
        const NeighborRecord &route =
            pendingNeighbors.routes[pendingNeighbors.offset + index];
        message->neighbors[index].id = route.id;
        message->neighbors[index].distance = route.distance;
    }

    if (!notify(messageCharacteristic, buffer.data(), buffer.size()))
        return false;
    pendingNeighbors.emptyPagePending = false;
    pendingNeighbors.offset += count;
    if (pendingNeighbors.offset >= pendingNeighbors.routes.size())
        pendingNeighbors = PendingNeighbors{};
    return true;
}

void Bluetooth::pumpMessageNotification()
{
    if (!deviceConnected || !notificationsEnabled())
        return;
    const uint32_t now = millis();
    if (static_cast<uint32_t>(now - lastNotificationAt) <
        NOTIFICATION_INTERVAL_MS)
        return;

    const bool sent =
        pumpControlNotification() ||
        pumpInboundNotification() ||
        pumpNeighborNotification();
    if (sent)
        lastNotificationAt = now;
}

#endif
