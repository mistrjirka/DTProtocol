#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
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
    bool transmitting = false;
    bool sleeping = false;
    bool standby_mode = false;
    void (*dio1_action)() = nullptr;

    uint16_t irq_flags = 0;
    uint16_t packet_length = 0;
    int finish_transmit_calls = 0;
    int read_data_calls = 0;
    int start_receive_calls = 0;
    int standby_calls = 0;
    int scan_channel_calls = 0;
    int rssi_read_calls = 0;
    bool trigger_rx_on_next_rssi = false;
    uint32_t last_cad_duration_us = 0;
    uint64_t modeled_busy_wait_us = 0;

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
        ++start_receive_calls;
        // SX1262 datasheet typical STBY_RC -> RX transition: ~83 us. RadioLib
        // waits for BUSY, so expose that latency to the MAC contract rather than
        // requiring application-side magic delays.
        if (!receiving) {
            constexpr uint32_t transition_us = 83;
            delayMicroseconds(transition_us);
            modeled_busy_wait_us += transition_us;
        }
        receiving = true;
        transmitting = false;
        sleeping = false;
        standby_mode = false;
        // RadioLib startReceiveCommon() restores RX IRQ mapping and clears IRQs.
        irq_flags = 0;
        return RADIOLIB_ERR_NONE;
    }
    int standby() {
        ++standby_calls;
        if (sleeping) {
            // Warm sleep wake to STBY_RC is about 340 us typical.
            constexpr uint32_t wake_us = 340;
            delayMicroseconds(wake_us);
            modeled_busy_wait_us += wake_us;
        }
        receiving = false;
        transmitting = false;
        sleeping = false;
        standby_mode = true;
        return RADIOLIB_ERR_NONE;
    }
    int sleep(bool) {
        receiving = false;
        transmitting = false;
        sleeping = true;
        standby_mode = false;
        return RADIOLIB_ERR_NONE;
    }

    int scanChannel() {
        ++scan_channel_calls;
        // RadioLib 6.x synchronous scanChannel() enters standby, performs CAD,
        // waits for CAD_DONE and then getChannelScanResult() clears CAD IRQs.
        receiving = false;
        transmitting = false;
        standby_mode = false;

        // Default SX126x CAD uses 4 symbols; the chip remains in RX for roughly
        // another 0.5 symbol for post-processing. At SF9/BW125 this is 18.432ms.
        const uint32_t sf = spreading_factor > 0 ? static_cast<uint32_t>(spreading_factor) : 9u;
        const float bw_khz = bandwidth > 0.0f ? bandwidth : 125.0f;
        const double symbol_us =
            (static_cast<double>(uint32_t{1} << sf) * 1000.0) /
            static_cast<double>(bw_khz);
        last_cad_duration_us = static_cast<uint32_t>(symbol_us * 4.5 + 0.5);
        delayMicroseconds(last_cad_duration_us);
        modeled_busy_wait_us += last_cad_duration_us;

        if (scan_channel_result == RADIOLIB_LORA_DETECTED)
            irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE | RADIOLIB_SX126X_IRQ_CAD_DETECTED;
        else
            irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE;
        if (dio1_action)
            dio1_action();

        // getChannelScanResult() clears CAD IRQ status before returning.
        irq_flags = 0;
        standby_mode = true;
        return scan_channel_result;
    }

    // RadioLib 6.x public SX126x IRQ accessor. Intentionally do not expose the
    // newer getIrqFlags()/clearIrqFlags() pair: the contract must catch API drift.
    uint16_t getIrqStatus() { return irq_flags; }

    int getRSSI(bool = false) {
        ++rssi_read_calls;
        if (trigger_rx_on_next_rssi) {
            trigger_rx_on_next_rssi = false;
            irq_flags = RADIOLIB_SX126X_IRQ_RX_DONE;
            if (packet_length == 0)
                packet_length = 8;
            if (dio1_action)
                dio1_action();
        }
        return rssi;
    }
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
        if (start_transmit_result != RADIOLIB_ERR_NONE)
            return start_transmit_result;
        // Typical STBY_RC -> TX transition is ~126 us; RadioLib waits on BUSY.
        constexpr uint32_t transition_us = 126;
        delayMicroseconds(transition_us);
        modeled_busy_wait_us += transition_us;
        receiving = false;
        transmitting = true;
        sleeping = false;
        standby_mode = false;
        return RADIOLIB_ERR_NONE;
    }
    int finishTransmit() {
        ++finish_transmit_calls;
        irq_flags = 0;
        transmitting = false;
        standby_mode = true;
        return RADIOLIB_ERR_NONE;
    }

    float getFrequencyError() { return 0.0f; }
};
