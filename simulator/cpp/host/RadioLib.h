#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_PREAMBLE_DETECTED -14
#define RADIOLIB_CHANNEL_FREE -15
#define RADIOLIB_LORA_DETECTED -702
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12

class SX1262 {
public:
    float frequency = 0.0f;
    int output_power = 0;
    float bandwidth = 0.0f;
    int spreading_factor = 0;
    int coding_rate = 0;
    uint8_t sync_word = 0;
    uint16_t preamble_length = 0;
    int rssi = -120;
    int start_transmit_result = RADIOLIB_ERR_NONE;
    int scan_channel_result = RADIOLIB_CHANNEL_FREE;
    bool receiving = false;
    bool sleeping = false;
    bool standby_mode = false;
    void (*dio1_action)() = nullptr;

    int setFrequency(float value) {
        frequency = value;
        return RADIOLIB_ERR_NONE;
    }
    int setOutputPower(int value) {
        output_power = value;
        return RADIOLIB_ERR_NONE;
    }
    int setBandwidth(float value) {
        bandwidth = value;
        return RADIOLIB_ERR_NONE;
    }
    int setSpreadingFactor(int value) {
        spreading_factor = value;
        return RADIOLIB_ERR_NONE;
    }
    int setCodingRate(int value) {
        coding_rate = value;
        return RADIOLIB_ERR_NONE;
    }
    int setSyncWord(uint8_t value) {
        sync_word = value;
        return RADIOLIB_ERR_NONE;
    }
    int setPreambleLength(uint16_t value) {
        preamble_length = value;
        return RADIOLIB_ERR_NONE;
    }
    void setDio1Action(void (*callback)()) { dio1_action = callback; }

    int startReceive() {
        receiving = true;
        sleeping = false;
        standby_mode = false;
        return RADIOLIB_ERR_NONE;
    }
    int standby() {
        receiving = false;
        sleeping = false;
        standby_mode = true;
        return RADIOLIB_ERR_NONE;
    }
    int sleep(bool) {
        receiving = false;
        sleeping = true;
        standby_mode = false;
        return RADIOLIB_ERR_NONE;
    }

    int scanChannel() { return scan_channel_result; }
    int getRSSI(bool = false) { return rssi; }
    uint32_t random(uint32_t max_value) {
        return max_value ? (0x1234u % max_value) : 0;
    }
    uint16_t getPacketLength(bool = true) { return 0; }
    int readData(uint8_t *, size_t) { return RADIOLIB_ERR_NONE; }

    int startTransmit(const uint8_t *, size_t) {
        return start_transmit_result;
    }
    int finishTransmit() { return RADIOLIB_ERR_NONE; }

    // Kept for source compatibility with older MAC implementations/audits.
    float getFrequencyError() { return 0.0f; }
};
