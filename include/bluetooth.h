#ifndef DTPK_BLUETOOTH_H
#define DTPK_BLUETOOTH_H

#include <BluetoothProtocol.h>

#if defined(ARDUINO_ARCH_ESP32) || defined(ESP32)
#define DTPK_BLE_AVAILABLE 1

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <DTPK.h>
#include <functional>
#include <deque>
#include <vector>

class Bluetooth
{
public:
    static Bluetooth *getInstance();
    static void initialize();

    bool setup();
    void loop();
    void startAdvertising();
    void stopAdvertising();
    void sendAckMessage(uint16_t originalMessageId, bool success, uint16_t ping);
    void sendInboundMessage(
        uint16_t senderId,
        const uint8_t *message,
        size_t messageLen);
    void sendNeighborsUpdate();

    void setDTPKPacketCallback(DTPK::PacketReceivedCallback callback)
    {
        dtpkCallback = callback;
    }

    bool deviceIsConnected() const { return deviceConnected; }
    bool isReady() const { return ready; }

    void setDeviceName(const char *name)
    {
        if (name && *name)
            deviceName = name;
    }

private:
    friend class DTPKBLEServerCallbacks;
    friend class DTPKBLEMessageCallbacks;

    static Bluetooth *instance;

    BLEServer *server = nullptr;
    BLECharacteristic *messageCharacteristic = nullptr;
    BLECharacteristic *neighborCountCharacteristic = nullptr;
    bool deviceConnected = false;
    bool oldDeviceConnected = false;
    bool ready = false;
    uint32_t disconnectionTime = 0;
    uint16_t messageCounter = 0;
    DTPK::PacketReceivedCallback dtpkCallback;
    static constexpr size_t MAX_QUEUED_NOTIFICATIONS = 128;
    std::deque<std::vector<uint8_t>> messageNotifications;
    const char *deviceName = "DTPK";

    Bluetooth() = default;
    void handleConnection(bool connected);
    void handleWrite(const std::string &value);
    void handleDTPKPacket(DTPKPacketGeneric *packet, uint16_t size);
    void periodicNeighborUpdate();
    uint16_t nextMessageId();
    bool notify(
        BLECharacteristic *characteristic,
        const uint8_t *bytes,
        size_t size);
    bool queueMessageNotification(
        std::vector<uint8_t> bytes, bool priority = false);
    void pumpMessageNotification();
};

#else
#define DTPK_BLE_AVAILABLE 0

class Bluetooth
{
public:
    static Bluetooth *getInstance()
    {
        static Bluetooth instance;
        return &instance;
    }
    static void initialize() {}
    bool setup() { return false; }
    void loop() {}
    void startAdvertising() {}
    void stopAdvertising() {}
    void sendAckMessage(uint16_t, bool, uint16_t) {}
    void sendInboundMessage(uint16_t, const uint8_t *, size_t) {}
    void sendNeighborsUpdate() {}
    void setDeviceName(const char *) {}
    bool deviceIsConnected() const { return false; }
    bool isReady() const { return false; }
};

#endif
#endif
