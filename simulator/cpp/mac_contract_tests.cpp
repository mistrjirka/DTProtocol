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
    uint8_t recommended_duty = 1;
    int spreading_factor = 9;

    if (argc > 1 && std::strcmp(argv[1], "sf8") == 0) {
        region = MACRegion::EU433;
        expected_frequency = 433.175f;
        expected_power = 10;
        expected_channels = 13;
        recommended_duty = 10;
        spreading_factor = 8;
    } else if (argc > 1 && std::strcmp(argv[1], "eu869-high-duty") == 0) {
        region = MACRegion::EU869_HIGH_DUTY;
        expected_frequency = 869.525f;
        expected_power = 20;
        expected_channels = 1;
        recommended_duty = 10;
    } else if (argc > 1 && std::strcmp(argv[1], "eu433") == 0) {
        region = MACRegion::EU433;
        expected_frequency = 433.175f;
        expected_power = 10;
        expected_channels = 13;
        recommended_duty = 10;
    }

    const bool strict_duty = argc > 2 && std::strcmp(argv[2], "strict-duty") == 0;

    MAC::initialize(
        radio,
        42,
        region,
        0,
        spreading_factor,
        125.0f,
        15,
        22,
        7);

    MAC *mac = MAC::getInstance();
    assert(mac != nullptr);
    assert(mac->getId() == 42);
    assert(mac->getRegion() == region);
    assert(mac->getNumberOfChannels() == expected_channels);
    assert(mac->recommendedRegionalDutyCyclePercent() == recommended_duty);

    // Region selection is practical by default: it configures frequency/power
    // but does not automatically impose the extremely restrictive fallback
    // duty limits. Strict throttling is an explicit policy choice.
    assert(mac->getDutyCycleLimitPercent() == 0);
    if (strict_duty)
        mac->setDutyCycleLimitPercent(recommended_duty);
    assert(mac->getDutyCycleLimitPercent() == (strict_duty ? recommended_duty : 0));

    assert(closeEnough(radio.frequency, expected_frequency));
    assert(closeEnough(radio.bandwidth, 125.0f));
    assert(radio.output_power == expected_power);
    assert(radio.spreading_factor == spreading_factor);
    assert(radio.coding_rate == 7);
    const uint16_t example_frame_bytes = 180;
    const uint32_t expected_airtime = static_cast<uint32_t>(std::ceil(
        MathExtension.timeOnAir(
            example_frame_bytes,
            DEFAULT_PREAMBLE_LENGTH,
            static_cast<uint8_t>(spreading_factor),
            125.0f,
            7)));
    assert(mac->estimateFrameAirtimeMs(example_frame_bytes) == expected_airtime);
    assert(mac->getNoiseFloorOfChannel(expected_channels) == 255);

    const uint32_t neighborExpiry =
        mac->recommendedNeighborExpiryMs(60'000, 12'000, 1'000);
    if (strict_duty && recommended_duty == 1) {
        assert(neighborExpiry > 180'000);
        assert(neighborExpiry < 190'000);
    } else {
        assert(neighborExpiry == 60'000);
    }

    const unsigned char payload[] = {1, 2, 3};

    mac->setMode(SENDING, true);
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_BUSY);

    // RSSI is the default CCA. A loud carrier must defer without running CAD.
    mac->setMode(RECEIVING, true);
    assert(!mac->isCadCarrierSenseEnabled());
    radio.rssi = -90;
    const int scansBeforeRssiBusy = radio.scan_channel_calls;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    assert(radio.scan_channel_calls == scansBeforeRssiBusy);
    assert(mac->getTransmitWaitMs() > 0);
    delay(mac->getTransmitWaitMs());
    radio.rssi = -120;

    // If RX_DONE arrives while RSSI is being sampled, CCA must defer and leave
    // the wake flag for loop() instead of erasing it at TX start.
    const int readsBeforeCcaRx = radio.read_data_calls;
    radio.packet_length = sizeof(MACHeader);
    radio.trigger_rx_on_next_rssi = true;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    mac->loop();
    assert(radio.read_data_calls == readsBeforeCcaRx + 1);
    assert(mac->getMode() == RECEIVING);
    delay(mac->getTransmitWaitMs());

    // CAD is optional and supplementary. When explicitly enabled, a detected
    // matching LoRa signal fails closed. The stub models the recommended
    // 4-symbol CAD plus ~0.5 symbol post-processing at the configured SF/BW.
    mac->setCadCarrierSenseEnabled(true);
    radio.scan_channel_result = RADIOLIB_LORA_DETECTED;
    const int scansBeforeCad = radio.scan_channel_calls;
    const uint64_t cadStartUs = micros();
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_CHANNEL_BUSY_TIMEOUT);
    assert(radio.scan_channel_calls == scansBeforeCad + 1);
    const uint64_t expectedCadUs = static_cast<uint64_t>(std::llround(
        4.5 * static_cast<double>(1u << spreading_factor) /
        125000.0 * 1000000.0));
    assert(radio.last_cad_duration_us + 500 >= expectedCadUs);
    assert(radio.last_cad_duration_us <= expectedCadUs + 500);
    assert(micros() - cadStartUs >= radio.last_cad_duration_us);
    assert(mac->getMode() == RECEIVING);
    delay(mac->getTransmitWaitMs());
    radio.scan_channel_result = RADIOLIB_CHANNEL_FREE;
    mac->setCadCarrierSenseEnabled(false);

    radio.start_transmit_result = -42;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_RADIO_ERROR);
    assert(mac->getMode() == RECEIVING);

    radio.start_transmit_result = RADIOLIB_ERR_NONE;
    const uint64_t busyBeforeTx = radio.modeled_busy_wait_us;
    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);
    assert(mac->getMode() == SENDING);
    assert(radio.dio1_action != nullptr);
    assert(radio.modeled_busy_wait_us >= busyBeforeTx + 126);

    // TX completion is classified from the radio IRQ register, not mutable
    // software state. Deliberately perturb software state before loop().
    const int finishBefore = radio.finish_transmit_calls;
    radio.irq_flags = RADIOLIB_SX126X_IRQ_TX_DONE;
    radio.dio1_action();
    mac->setMode(IDLE, true);
    mac->loop();
    assert(radio.finish_transmit_calls == finishBefore + 1);
    assert(mac->getMode() == RECEIVING);

    // RX_DONE is likewise classified by the hardware IRQ bits.
    const int readsBefore = radio.read_data_calls;
    radio.packet_length = sizeof(MACHeader);
    radio.irq_flags = RADIOLIB_SX126X_IRQ_RX_DONE;
    radio.dio1_action();
    mac->setMode(IDLE, true);
    mac->loop();
    assert(radio.read_data_calls == readsBefore + 1);
    assert(mac->getMode() == RECEIVING);

    // RadioLib 6.x does not expose clearIrqStatus publicly. For a stray CAD or
    // error wakeup the MAC must restore RX via startReceive(), which remaps and
    // clears IRQ state through the public driver path.
    const int rxStartsBeforeStray = radio.start_receive_calls;
    const int readsBeforeStray = radio.read_data_calls;
    radio.irq_flags = RADIOLIB_SX126X_IRQ_CAD_DONE;
    radio.dio1_action();
    mac->loop();
    assert(radio.start_receive_calls == rxStartsBeforeStray + 1);
    assert(radio.read_data_calls == readsBeforeStray);
    assert(radio.irq_flags == 0);
    assert(mac->getMode() == RECEIVING);

    const uint32_t wait = mac->getTransmitWaitMs();
    if (strict_duty) {
        assert(wait > 0);
        assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_DUTY_CYCLE);
        delay(wait);
        assert(mac->getTransmitWaitMs() == 0);
    } else {
        assert(wait == 0);
    }

    assert(mac->sendData(7, const_cast<unsigned char *>(payload), sizeof(payload), 100) == MAC_SEND_OK);
    radio.irq_flags = RADIOLIB_SX126X_IRQ_TX_DONE;
    radio.dio1_action();
    mac->loop();
    assert(mac->getMode() == RECEIVING);

    return 0;
}
