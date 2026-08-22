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

    // SF9/BW125 full-frame airtime makes the 1% profile legally silent for
    // about 170 s after a large transmission. Liveness must expand accordingly;
    // 10% profiles remain safely inside the original 30 s timeout.
    const uint32_t neighborExpiry =
        mac->recommendedNeighborExpiryMs(30'000, 12'000, 1'000);
    if (expected_duty == 1) {
        assert(neighborExpiry > 180'000);
        assert(neighborExpiry < 190'000);
    } else {
        assert(neighborExpiry == 30'000);
    }

    const unsigned char payload[] = {1, 2, 3};

    mac->setMode(SENDING, true);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_BUSY);

    mac->setMode(RECEIVING, true);
    radio.scan_channel_result = RADIOLIB_LORA_DETECTED;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    assert(mac->getTransmitWaitMs() > 0);
    delay(mac->getTransmitWaitMs());
    radio.scan_channel_result = RADIOLIB_CHANNEL_FREE;

    radio.start_transmit_result = -42;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_RADIO_ERROR);
    assert(mac->getMode() == RECEIVING);

    radio.start_transmit_result = RADIOLIB_ERR_NONE;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);
    assert(mac->getMode() == SENDING);
    assert(radio.dio1_action != nullptr);

    const int finishBefore = radio.finish_transmit_calls;
    radio.irq_flags = RADIOLIB_SX126X_IRQ_TX_DONE;
    radio.dio1_action();
    mac->setMode(IDLE, true);
    mac->loop();
    assert(radio.finish_transmit_calls == finishBefore + 1);
    assert(mac->getMode() == RECEIVING);

    const int readsBefore = radio.read_data_calls;
    radio.packet_length = sizeof(MACHeader);
    radio.irq_flags = RADIOLIB_SX126X_IRQ_RX_DONE;
    radio.dio1_action();
    mac->setMode(IDLE, true);
    mac->loop();
    assert(radio.read_data_calls == readsBefore + 1);
    assert(mac->getMode() == RECEIVING);

    const int clearBefore = radio.clear_irq_calls;
    const int readsBeforeCad = radio.read_data_calls;
    radio.irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE;
    radio.dio1_action();
    mac->setMode(RECEIVING, false);
    mac->loop();
    assert(radio.clear_irq_calls == clearBefore + 1);
    assert(radio.read_data_calls == readsBeforeCad);
    assert(mac->getMode() == RECEIVING);

    const uint32_t wait = mac->getTransmitWaitMs();
    assert(wait > 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_DUTY_CYCLE);

    delay(wait);
    assert(mac->getTransmitWaitMs() == 0);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);

    radio.irq_flags = RADIOLIB_SX126X_IRQ_TX_DONE;
    radio.dio1_action();
    mac->loop();
    assert(mac->getMode() == RECEIVING);

    return 0;
}
