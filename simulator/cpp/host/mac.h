#pragma once

#ifndef MAC_LAYER_H
#define MAC_LAYER_H

#include "Arduino.h"
#include <cstdint>
#include <functional>
#include <vector>

enum State {
    IDLE,
    SENDING,
    RECEIVING,
    SLEEPING
};

typedef struct __attribute__((packed)) {
    uint32_t crc32;
    uint16_t sender;
    uint16_t target;
    unsigned char data[];
} MACPacket;

typedef struct __attribute__((packed)) {
    uint32_t crc32;
    uint16_t sender;
    uint16_t target;
} MACHeader;

class MAC {
public:
    using PacketReceivedCallback =
        std::function<void(MACPacket *packet, uint16_t size, uint32_t crcCalculated)>;
    using TransmitDone = std::function<void(void)>;

    static void initialize(int id);
    static MAC *getInstance();

    void setRXCallback(PacketReceivedCallback callback);
    void setRXAlienCallback(PacketReceivedCallback callback);
    uint8_t sendData(uint16_t target, unsigned char *data,
                     uint8_t size, uint32_t timeout = 5000);
    void loop();
    uint32_t random();
    void setMode(State state, bool force = true);
    State getMode();
    uint16_t getId();
    void setTransmitDone(TransmitDone callback);

    // Host-only entry points. The production protocol never calls these.
    bool hostInject(uint16_t sender, uint16_t target,
                    const std::vector<uint8_t> &payload);
    void hostPhyDone(uint64_t token);

private:
    explicit MAC(uint16_t id);
    static MAC *instance_;
    static State state_;

    uint16_t id_;
    uint64_t active_tx_token_ = 0;
    PacketReceivedCallback rx_callback_;
    PacketReceivedCallback rx_alien_callback_;
    TransmitDone transmit_done_;
};

#endif
