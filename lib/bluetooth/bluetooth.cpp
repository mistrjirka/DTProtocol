#include <bluetooth.h>

#if DTPK_BLE_AVAILABLE

#include <algorithm>
#include <cstring>
#include <vector>

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

bool Bluetooth::setup()
{
    if (ready)
        return true;
    if (!DTPK::getInstance())
        return false;

    BLEDevice::init(deviceName);
    BLEDevice::setMTU(BLE_REQUESTED_MTU);
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
    deviceConnected = connected;
    if (connected)
        sendNeighborsUpdate();
    else
    {
        disconnectionTime = millis();
        messageNotifications.clear();
    }
}

void Bluetooth::loop()
{
    DTPK *dtpk = DTPK::getInstance();
    if (dtpk)
        dtpk->loop();

    if (!deviceConnected && oldDeviceConnected &&
        static_cast<uint32_t>(millis() - disconnectionTime) >= 250u)
    {
        startAdvertising();
        oldDeviceConnected = false;
    }
    else if (deviceConnected && !oldDeviceConnected)
    {
        oldDeviceConnected = true;
    }

    periodicNeighborUpdate();
    pumpMessageNotification();
}

bool Bluetooth::notify(
    BLECharacteristic *characteristic,
    const uint8_t *bytes,
    size_t size)
{
    if (!ready || !deviceConnected || !characteristic || !bytes ||
        size == 0 || size > BLE_MAX_NOTIFICATION_BYTES)
        return false;
    characteristic->setValue(const_cast<uint8_t *>(bytes), size);
    characteristic->notify();
    return true;
}


bool Bluetooth::queueMessageNotification(
    std::vector<uint8_t> bytes, bool priority)
{
    if (!deviceConnected || bytes.empty())
        return false;
    if (messageNotifications.size() >= MAX_QUEUED_NOTIFICATIONS)
    {
        if (!priority)
            return false;
        messageNotifications.pop_back();
    }
    if (priority)
        messageNotifications.push_front(std::move(bytes));
    else
        messageNotifications.push_back(std::move(bytes));
    return true;
}

void Bluetooth::pumpMessageNotification()
{
    if (!deviceConnected || messageNotifications.empty())
        return;
    std::vector<uint8_t> &bytes = messageNotifications.front();
    if (notify(messageCharacteristic, bytes.data(), bytes.size()))
        messageNotifications.pop_front();
}

void Bluetooth::handleWrite(const std::string &value)
{
    BLEOutboundView view;
    if (!parseBLEOutboundMessage(
            reinterpret_cast<const uint8_t *>(value.data()),
            value.size(),
            DTPK::maximumMessageSize(),
            view))
    {
        uint16_t messageId = 0;
        if (value.size() >= sizeof(BLEMessageHeader))
        {
            BLEMessageHeader header{};
            memcpy(&header, value.data(), sizeof(header));
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

    const uint16_t messageId = view.header.messageId;
    dtpk->sendPacket(
        view.recipientId,
        const_cast<unsigned char *>(view.payload),
        view.payloadSize,
        60000,
        true,
        [messageId](uint8_t result, uint16_t ping) {
            Bluetooth::getInstance()->sendAckMessage(
                messageId, result != 0, ping);
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
    queueMessageNotification(std::move(buffer), true);
}

void Bluetooth::sendInboundMessage(
    uint16_t senderId,
    const uint8_t *payload,
    size_t payloadSize)
{
    if (payloadSize > UINT16_MAX || (payloadSize > 0 && !payload))
        return;

    const uint16_t logicalId = nextMessageId();
    if (payloadSize <= maxBLEInboundPayloadPerNotification())
    {
        std::vector<uint8_t> buffer(sizeof(BLEInboundMessage) + payloadSize);
        BLEInboundMessage *message =
            reinterpret_cast<BLEInboundMessage *>(buffer.data());
        message->header.type = BLE_MSG_TYPE_INBOUND;
        message->header.messageId = logicalId;
        message->header.length = static_cast<uint16_t>(buffer.size());
        message->senderId = senderId;
        if (payloadSize)
            memcpy(message->data, payload, payloadSize);
        queueMessageNotification(std::move(buffer));
        return;
    }

    const size_t chunkCapacity = maxBLEInboundFragmentPayload();
    for (size_t offset = 0; offset < payloadSize; offset += chunkCapacity)
    {
        const size_t chunk = std::min(chunkCapacity, payloadSize - offset);
        std::vector<uint8_t> buffer(
            sizeof(BLEInboundFragmentMessage) + chunk);
        BLEInboundFragmentMessage *message =
            reinterpret_cast<BLEInboundFragmentMessage *>(buffer.data());
        message->header.type = BLE_MSG_TYPE_INBOUND_FRAGMENT;
        message->header.messageId = logicalId;
        message->header.length = static_cast<uint16_t>(buffer.size());
        message->senderId = senderId;
        message->totalLength = static_cast<uint16_t>(payloadSize);
        message->offset = static_cast<uint16_t>(offset);
        memcpy(message->data, payload + offset, chunk);
        queueMessageNotification(std::move(buffer));
    }
}

void Bluetooth::sendNeighborsUpdate()
{
    DTPK *dtpk = DTPK::getInstance();
    if (!dtpk)
        return;

    const std::vector<NeighborRecord> neighbors = dtpk->getNeighbours();
    uint16_t fullCount = static_cast<uint16_t>(
        std::min<size_t>(neighbors.size(), UINT16_MAX));
    if (neighborCountCharacteristic)
    {
        neighborCountCharacteristic->setValue(
            reinterpret_cast<uint8_t *>(&fullCount),
            sizeof(fullCount));
        if (deviceConnected)
            neighborCountCharacteristic->notify();
    }

    const size_t count = std::min<size_t>(
        neighbors.size(), maxBLENeighborsPerNotification());
    std::vector<uint8_t> buffer(
        sizeof(BLENeighborsMessage) + count * sizeof(BLENeighborInfo));
    BLENeighborsMessage *message =
        reinterpret_cast<BLENeighborsMessage *>(buffer.data());
    message->header.type = BLE_MSG_TYPE_NEIGHBORS;
    message->header.messageId = nextMessageId();
    message->header.length = static_cast<uint16_t>(buffer.size());
    message->count = static_cast<uint8_t>(count);

    for (size_t index = 0; index < count; ++index)
    {
        message->neighbors[index].id = neighbors[index].id;
        message->neighbors[index].distance = neighbors[index].distance;
    }
    queueMessageNotification(std::move(buffer));
}

#endif
