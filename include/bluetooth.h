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
#include <atomic>
#include <deque>
#include <functional>
#include <vector>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

struct BluetoothDiagnostics
{
    uint32_t droppedWrites = 0;
    uint32_t droppedInboundMessages = 0;
    uint32_t droppedControlNotifications = 0;
};

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
    uint16_t negotiatedMtu() const;
    BluetoothDiagnostics getDiagnostics() const;

    void setDeviceName(const char *name)
    {
        if (name && *name)
            deviceName = name;
    }

private:
    friend class DTPKBLEServerCallbacks;
    friend class DTPKBLEMessageCallbacks;

    struct PendingInbound
    {
        uint16_t messageId = 0;
        uint16_t senderId = 0;
        size_t offset = 0;
        std::vector<uint8_t> payload;
    };

    struct PendingNeighbors
    {
        bool active = false;
        bool emptyPagePending = false;
        size_t offset = 0;
        std::vector<NeighborRecord> routes;
    };

    static Bluetooth *instance;
    static constexpr size_t MAX_PENDING_WRITES = 2;
    static constexpr size_t MAX_PENDING_WRITE_ERRORS = 8;
    static constexpr size_t MAX_CONTROL_NOTIFICATIONS = 32;
    static constexpr size_t MAX_PENDING_INBOUND = 4;
    static constexpr uint32_t NOTIFICATION_INTERVAL_MS = 30;

    BLEServer *server = nullptr;
    BLECharacteristic *messageCharacteristic = nullptr;
    BLECharacteristic *neighborCountCharacteristic = nullptr;
    bool deviceConnected = false;
    bool advertisingRestartPending = false;
    bool ready = false;
    uint32_t disconnectionTime = 0;
    uint32_t lastNotificationAt = 0;
    uint16_t messageCounter = 0;
    DTPK::PacketReceivedCallback dtpkCallback;
    std::atomic<bool> desiredConnected{false};
    std::atomic<bool> connectionStateDirty{false};
    std::atomic<bool> neighborsUpdateRequested{false};
    std::atomic<uint32_t> droppedWrites{0};
    std::atomic<uint32_t> droppedInboundMessages{0};
    std::atomic<uint32_t> droppedControlNotifications{0};
    SemaphoreHandle_t callbackMutex = nullptr;
    std::deque<std::vector<uint8_t>> pendingWrites;
    std::deque<uint16_t> pendingWriteErrors;
    std::deque<std::vector<uint8_t>> controlNotifications;
    std::deque<PendingInbound> inboundNotifications;
    PendingNeighbors pendingNeighbors;
    const char *deviceName = "DTPK";

    Bluetooth();
    void handleConnection(bool connected);
    void handleWrite(const std::string &value);
    void handleDTPKPacket(DTPKPacketGeneric *packet, uint16_t size);
    void applyConnectionState();
    void processPendingWrite();
    void periodicNeighborUpdate();
    void prepareNeighborsUpdate();
    uint16_t nextMessageId();
    size_t notificationCapacity() const;
    bool notificationsEnabled() const;
    bool notify(
        BLECharacteristic *characteristic,
        const uint8_t *bytes,
        size_t size);
    bool queueControlNotification(
        std::vector<uint8_t> bytes, bool priority = false);
    bool pumpControlNotification();
    bool pumpInboundNotification();
    bool pumpNeighborNotification();
    void pumpMessageNotification();
    void clearLoopOwnedQueues();
    void clearCallbackQueues();
};

#else
#define DTPK_BLE_AVAILABLE 0

struct BluetoothDiagnostics
{
    uint32_t droppedWrites = 0;
    uint32_t droppedInboundMessages = 0;
    uint32_t droppedControlNotifications = 0;
};

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
    uint16_t negotiatedMtu() const { return BLE_DEFAULT_ATT_MTU; }
    BluetoothDiagnostics getDiagnostics() const { return {}; }
};

#endif
#endif
