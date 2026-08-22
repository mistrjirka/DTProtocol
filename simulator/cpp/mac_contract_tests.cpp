#include <mac.h>

#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace {

bool closeEnough(float a, float b, float eps = 0.001f) {
    return std::fabs(a - b) <= eps;
}

} // namespace

int main(int argc, char **argv) {
    SX1262 radio;

    MACRegion region = MACRegion::EU868;
    float expected_frequency = 868.100f;
    int expected_power = 13;
    uint8_t expected_channels = 3;
    uint8_t expected_duty = 1;

    if (argc > 1 && std::strcmp(argv[1], "eu869-high-duty") == 0) {
        region = MACRegion::EU869_HIGH_DUTY;
        expected_frequency = 869.525f;
        expected_power = 20;
        expected_channels = 1;
        expected_duty = 10;
    } else if (argc > 1 && std::strcmp(argv[1], "eu433") == 0) {
        region = MACRegion::EU433;
        expected_frequency = 433.175f;
        expected_power = 10;
        expected_channels = 13;
        expected_duty = 10;
    }

    // Intentionally excessive requested power verifies regional conducted caps.
    MAC::initialize(
        radio,
        42,
        region,
        0,
        9,
        125.0f,
        15,
        22,
        7);

    MAC *mac = MAC::getInstance();
    assert(mac != nullptr);
    assert(mac->getId() == 42);
    assert(mac->getRegion() == region);
    assert(mac->getNumberOfChannels() == expected_channels);
    assert(mac->getFallbackDutyCyclePercent() == expected_duty);
    assert(closeEnough(radio.frequency, expected_frequency));
    assert(closeEnough(radio.bandwidth, 125.0f));
    assert(radio.output_power == expected_power);
    assert(radio.spreading_factor == 9);
    assert(radio.coding_rate == 7);
    assert(mac->getNoiseFloorOfChannel(expected_channels) == 255);

    const unsigned char payload[] = {1, 2, 3};

    // Busy must be observable rather than silently reported as success.
    mac->setMode(SENDING, true);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_BUSY);

    // CAD detects LoRa activity even when RSSI remains below the energy threshold.
    mac->setMode(RECEIVING, true);
    radio.scan_channel_result = RADIOLIB_LORA_DETECTED;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    assert(mac->getTransmitWaitMs() > 0);
    delay(mac->getTransmitWaitMs());
    radio.scan_channel_result = RADIOLIB_CHANNEL_FREE;

    // Synchronous RadioLib TX failure must restore RX instead of wedging.
    radio.start_transmit_result = -42;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_RADIO_ERROR);
    assert(mac->getMode() == RECEIVING);

    radio.start_transmit_result = RADIOLIB_ERR_NONE;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);
    assert(mac->getMode() == SENDING);

    // Finish TX and verify the regional duty-cycle policy is non-blocking.
    assert(radio.dio1_action != nullptr);
    radio.dio1_action();
    mac->loop();
    assert(mac->getMode() == RECEIVING);
    const uint32_t wait = mac->getTransmitWaitMs();
    assert(wait > 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_DUTY_CYCLE);

    delay(wait);
    assert(mac->getTransmitWaitMs() == 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);

    return 0;
}
