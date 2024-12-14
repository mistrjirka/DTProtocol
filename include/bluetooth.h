#ifndef BLUETOOTH_H
#define BLUETOOTH_H

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <functional>
#include <DTPK.h>

#define BLE_MSG_TYPE_OUTBOUND     0x01
#define BLE_MSG_TYPE_ACK          0x02
#define BLE_MSG_TYPE_INBOUND      0x03
#define BLE_MSG_TYPE_NEIGHBORS    0x04

#define WATCH_SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define MSG_CHAR_UUID            "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define NEIGHCOUNT_CHAR_UUID     "beb5483e-36e1-4688-b7f5-ea07361b26a9"

// Message structures
#pragma pack(push, 1)
struct BLEMessageHeader {
    uint8_t type;
    uint16_t messageId;
    uint16_t length;
};

struct BLEOutboundMessage {
    BLEMessageHeader header;
    uint16_t recipientId;
    char data[];
};

struct BLEInboundMessage {
    BLEMessageHeader header;
    uint16_t senderId;
    char data[];
};

struct BLEAckMessage {
    BLEMessageHeader header;
    uint16_t originalMessageId;
    uint8_t success;
    uint16_t ping;
};

struct BLENeighborInfo {
    uint16_t id;
    uint16_t distance;
};

struct BLENeighborsMessage {
    BLEMessageHeader header;
    uint8_t count;
    BLENeighborInfo neighbors[];
};
#pragma pack(pop)

class Bluetooth {
public:
    static Bluetooth* getInstance();
    static void initialize();
    
    void setup();
    void loop();
    void startAdvertising();
    void stopAdvertising();
    void updateNeighborsList();
    void sendMessage(const char* message);
    void sendOutboundMessage(uint16_t recipientId, uint16_t messageId, const char* message);
    void sendInboundMessage(uint16_t senderId, const char* message, size_t messageLen);
    void sendAckMessage(uint16_t originalMessageId, bool success, uint16_t ping);
    void sendNeighborsUpdate();
    
    // Set callback for received DTPK packets
    void setDTPKPacketCallback(DTPK::PacketReceivedCallback callback) {
        dtpkCallback = callback;
    }

    bool deviceIsConnected() {
        return deviceConnected;
    }

    void setDeviceName(const char* name) {
        deviceName = name;
    }

private:
    friend class WatchServerCallbacks;  // Add this line to grant access
    friend class MsgCharacteristicCallbacks; // Also add this for consistency
    int32_t disconnectionTime;
    static Bluetooth* instance;
    BLEServer* pServer;
    BLECharacteristic* pMsgCharacteristic;
    BLECharacteristic* pNeighCountCharacteristic;
    bool deviceConnected;
    bool oldDeviceConnected;
    DTPK::PacketReceivedCallback dtpkCallback;
    const char* deviceName = "LoraWatch"; // Default name

    Bluetooth();  // Private constructor
    void handleConnection(bool connected);
    void handleDTPKPacket(DTPKPacketGenericReceive* packet, uint16_t size);
    void periodicNeighborUpdate();
};

#endif
