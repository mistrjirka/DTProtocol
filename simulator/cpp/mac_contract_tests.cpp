#include <mac.h>

#include <cassert>
#include <cmath>
#include <cstdint>

namespace {

bool closeEnough(float a, float b, float eps = 0.001f) {
    return std::fabs(a - b) <= eps;
}

} // namespace

int main() {
    SX1262 radio;

    // Use an intentionally excessive requested power to verify the EU868
    // profile applies its conservative conducted-power cap.
    MAC::initialize(
        radio,
        42,
        MACRegion::EU868,
        0,
        9,
        125.0f,
        15,
        22,
        7);

    MAC *mac = MAC::getInstance();
    assert(mac != nullptr);
    assert(mac->getId() == 42);
    assert(mac->getRegion() == MACRegion::EU868);
    assert(mac->getNumberOfChannels() == 3);
    assert(closeEnough(radio.frequency, 868.100f));
    assert(closeEnough(radio.bandwidth, 125.0f));
    assert(radio.output_power == 13);
    assert(radio.spreading_factor == 9);
    assert(radio.coding_rate == 7);
    assert(mac->getNoiseFloorOfChannel(3) == 255);

    const unsigned char payload[] = {1, 2, 3};

    // Busy must be observable rather than silently reported as success.
    mac->setMode(SENDING, true);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_BUSY);

    // CAD detects LoRa activity even when RSSI itself remains below the energy
    // threshold. v2 must return quickly and impose randomized retry backoff.
    mac->setMode(RECEIVING, true);
    radio.scan_channel_result = RADIOLIB_LORA_DETECTED;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    assert(mac->getTransmitWaitMs() > 0);
    delay(mac->getTransmitWaitMs());
    radio.scan_channel_result = RADIOLIB_CHANNEL_FREE;

    // A synchronous RadioLib startTransmit failure must return an error and
    // restore RX state instead of wedging forever in SENDING.
    radio.start_transmit_result = -42;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_RADIO_ERROR);
    assert(mac->getMode() == RECEIVING);

    // A successful start enters SENDING until its IRQ is serviced.
    radio.start_transmit_result = RADIOLIB_ERR_NONE;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);
    assert(mac->getMode() == SENDING);

    // Simulate TX-done IRQ. EU868 must then remain non-blockingly unavailable
    // until the conservative 1% duty-cycle off-time expires.
    assert(radio.dio1_action != nullptr);
    radio.dio1_action();
    mac->loop();
    assert(mac->getMode() == RECEIVING);
    assert(mac->getTransmitWaitMs() > 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_DUTY_CYCLE);

    delay(mac->getTransmitWaitMs());
    assert(mac->getTransmitWaitMs() == 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);

    return 0;
}
