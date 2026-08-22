#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_PREAMBLE_DETECTED -14
#define RADIOLIB_CHANNEL_FREE -15
#define RADIOLIB_LORA_DETECTED -702
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12

#define RADIOLIB_SX126X_IRQ_TX_DONE 0x0001u
#define RADIOLIB_SX126X_IRQ_RX_DONE 0x0002u
#define RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED 0x0004u
#define RADIOLIB_SX126X_IRQ_HEADER_ERR 0x0020u
#define RADIOLIB_SX126X_IRQ_CRC_ERR 0x0040u
#define RADIOLIB_SX126X_IRQ_CAD_DONE 0x0080u
#define RADIOLIB_SX126X_IRQ_CAD_DETECTED 0x0100u
#define RADIOLIB_SX126X_IRQ_TIMEOUT 0x0200u
#define RADIOLIB_SX126X_IRQ_ALL 0x03ffu

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

    uint32_t irq_flags = 0;
    uint16_t packet_length = 0;
    int finish_transmit_calls = 0;
    int read_data_calls = 0;
    int clear_irq_calls = 0;

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
        irq_flags = 0;
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

    int scanChannel() {
        if (scan_channel_result == RADIOLIB_LORA_DETECTED)
            irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE | RADIOLIB_SX126X_IRQ_CAD_DETECTED;
        else if (scan_channel_result == RADIOLIB_CHANNEL_FREE)
            irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE;
        return scan_channel_result;
    }
    uint32_t getIrqFlags() { return irq_flags; }
    int clearIrqFlags(uint32_t flags) {
        irq_flags &= ~flags;
        ++clear_irq_calls;
        return RADIOLIB_ERR_NONE;
    }

    int getRSSI(bool = false) { return rssi; }
    uint32_t random(uint32_t max_value) {
        return max_value ? (0x1234u % max_value) : 0;
    }
    uint16_t getPacketLength(bool = true) { return packet_length; }
    int readData(uint8_t *data, size_t len) {
        ++read_data_calls;
        if (data && len) std::memset(data, 0, len);
        irq_flags = 0;
        return RADIOLIB_ERR_NONE;
    }

    int startTransmit(const uint8_t *, size_t) {
        irq_flags = 0;
        return start_transmit_result;
    }
    int finishTransmit() {
        ++finish_transmit_calls;
        irq_flags = 0;
        return RADIOLIB_ERR_NONE;
    }

    float getFrequencyError() { return 0.0f; }
};
