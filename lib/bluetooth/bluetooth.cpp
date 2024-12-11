#include "bluetooth.h"

Bluetooth* Bluetooth::instance = nullptr;

// DTPK callback handler
void dtpkPacketReceived(DTPKPacketGenericReceive* packet, uint16_t size) {
    // Forward received packet to BLE client
    if (Bluetooth::getInstance()->deviceIsConnected()) {
        Bluetooth::getInstance()->sendInboundMessage(packet->originalSender, (const char*)packet->data);
    }
}

class WatchServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
        Bluetooth::getInstance()->handleConnection(true);
    };

    void onDisconnect(BLEServer* pServer) {
        Bluetooth::getInstance()->handleConnection(false);
    }
};

class MsgCharacteristicCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
        std::string value = pCharacteristic->getValue();
        if (value.length() < sizeof(BLEMessageHeader)) return;
        
        BLEMessageHeader* header = (BLEMessageHeader*)value.c_str();
        switch(header->type) {
            case BLE_MSG_TYPE_OUTBOUND: {
                BLEOutboundMessage* msg = (BLEOutboundMessage*)value.c_str();
                if (value.length() >= sizeof(BLEOutboundMessage)) {
                    DTPK::getInstance()->sendPacket(
                        msg->recipientId,
                        (unsigned char*)msg->data, 
                        value.length() - sizeof(BLEOutboundMessage), 
                        10000, 
                        true,
                        [messageId = msg->header.messageId](uint8_t result, uint16_t ping) {
                            Bluetooth::getInstance()->sendAckMessage(messageId, result != 0, ping);
                        }
                    );
                }
                break;
            }
            // Add other message type handlers here
        }
    }
};

Bluetooth* Bluetooth::getInstance() {
    if (!instance) {
        instance = new Bluetooth();
    }
    return instance;
}

void Bluetooth::initialize() {
    getInstance();
}

Bluetooth::Bluetooth() {
    deviceConnected = false;
    oldDeviceConnected = false;
    pServer = nullptr;
    pMsgCharacteristic = nullptr;
    pNeighCountCharacteristic = nullptr;
}

void Bluetooth::setup() {
    BLEDevice::init("LoraWatch");
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new WatchServerCallbacks());

    BLEService *pService = pServer->createService(WATCH_SERVICE_UUID);

    pMsgCharacteristic = pService->createCharacteristic(
        MSG_CHAR_UUID,
        BLECharacteristic::PROPERTY_READ |
        BLECharacteristic::PROPERTY_WRITE
    );
    pMsgCharacteristic->setCallbacks(new MsgCharacteristicCallbacks());

    pNeighCountCharacteristic = pService->createCharacteristic(
        NEIGHCOUNT_CHAR_UUID,
        BLECharacteristic::PROPERTY_READ
    );

    pService->start();
    startAdvertising();

    // Register DTPK callback
    DTPK::getInstance()->setPacketReceivedCallback(dtpkPacketReceived);
}

void Bluetooth::startAdvertising() {
    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(WATCH_SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);  
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
}

void Bluetooth::stopAdvertising() {
    BLEDevice::stopAdvertising();
}

void Bluetooth::handleConnection(bool connected) {
    deviceConnected = connected;
    if (connected) {
        sendNeighborsUpdate();
        printf("Device connected\n");
    }
}

void Bluetooth::loop() {
    // Handle BLE connection state
    if (!deviceConnected && oldDeviceConnected) {
        delay(500);
        startAdvertising();
        oldDeviceConnected = deviceConnected;
    }
    if (deviceConnected && !oldDeviceConnected) {
        oldDeviceConnected = deviceConnected;
    }
    
    periodicNeighborUpdate();
}

void Bluetooth::periodicNeighborUpdate() {
    static uint32_t lastUpdate = 0;
    if (deviceConnected && (millis() - lastUpdate > 5000)) {
        sendNeighborsUpdate();
        lastUpdate = millis();
    }
}

void Bluetooth::sendAckMessage(uint16_t originalMessageId, bool success, uint16_t ping) {
    if (!deviceConnected) return;
    
    BLEAckMessage msg;
    msg.header.type = BLE_MSG_TYPE_ACK;
    msg.header.messageId = millis();
    msg.header.length = sizeof(BLEAckMessage);
    msg.originalMessageId = originalMessageId;
    msg.success = success;
    msg.ping = ping;
    
    pMsgCharacteristic->setValue((uint8_t*)&msg, sizeof(msg));
    pMsgCharacteristic->notify();
}

void Bluetooth::sendInboundMessage(uint16_t senderId, const char* message) {
    if (!deviceConnected) return;
    
    size_t messageLen = strlen(message);
    size_t totalSize = sizeof(BLEInboundMessage) + messageLen;
    uint8_t* buffer = (uint8_t*)malloc(totalSize);
    
    BLEInboundMessage* msg = (BLEInboundMessage*)buffer;
    msg->header.type = BLE_MSG_TYPE_INBOUND;
    msg->header.messageId = millis(); // Use timestamp as message ID
    msg->header.length = totalSize;
    msg->senderId = senderId;
    memcpy(msg->data, message, messageLen);
    
    pMsgCharacteristic->setValue(buffer, totalSize);
    pMsgCharacteristic->notify();
    free(buffer);
}

void Bluetooth::sendNeighborsUpdate() {
    if (!deviceConnected) return;
    
    auto neighbors = DTPK::getInstance()->getNeighbours();
    size_t totalSize = sizeof(BLENeighborsMessage) + neighbors.size() * sizeof(BLENeighborInfo);
    uint8_t* buffer = (uint8_t*)malloc(totalSize);
    
    BLENeighborsMessage* msg = (BLENeighborsMessage*)buffer;
    msg->header.type = BLE_MSG_TYPE_NEIGHBORS;
    msg->header.messageId = millis();
    msg->header.length = totalSize;
    msg->count = neighbors.size();
    
    int i = 0;
    for (const auto& neighbor : neighbors) {
        msg->neighbors[i].id = neighbor.id;
        msg->neighbors[i].distance = neighbor.distance;
        i++;
    }
    
    pMsgCharacteristic->setValue(buffer, totalSize);
    pMsgCharacteristic->notify();
    free(buffer);
}

// ... Rest of the methods from watch_setup.cpp can be copied here, 
// just remove the 'static' keyword and add 'Bluetooth::' prefix ...
