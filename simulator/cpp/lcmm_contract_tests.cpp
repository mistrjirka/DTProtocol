#include "host/host_runtime.h"
#include <lcmm.h>
#include <mac.h>

#include <cassert>
#include <cstdint>
#include <cstdlib>

int main() {
    hostsim::reset(1, 123);
    MAC::initialize(1);

    LCMM::initialize(
        [](LCMMPacketDataReceive *packet, uint32_t) {
            std::free(packet);
        },
        [](uint16_t, bool) {});

    LCMM *lcmm = LCMM::getInstance();
    assert(lcmm != nullptr);

    unsigned char payload[] = {0x11, 0x22, 0x33, 0x44};

    // A regulatory/carrier denial is not an RF failure. The owner keeps the
    // payload and retries later, so no failure callback may be emitted here.
    int transientCallbacks = 0;
    hostsim::set_next_send_result(MAC_SEND_DUTY_CYCLE, 0);
    const uint16_t transientId = lcmm->sendPacketSingle(
        true,
        2,
        payload,
        sizeof(payload),
        [&](uint16_t, bool) { ++transientCallbacks; },
        1000,
        3);
    assert(transientId == 0);
    assert(transientCallbacks == 0);
    assert(lcmm->getLastSendResult() == MAC_SEND_DUTY_CYCLE);
    assert(!lcmm->isSending());

    // A genuine radio error is fatal for this submission and must be surfaced
    // immediately rather than retried forever as if it were channel contention.
    int fatalCallbacks = 0;
    bool fatalSuccess = true;
    hostsim::set_next_send_result(MAC_SEND_RADIO_ERROR, 0);
    const uint16_t fatalId = lcmm->sendPacketSingle(
        true,
        2,
        payload,
        sizeof(payload),
        [&](uint16_t, bool success) {
            ++fatalCallbacks;
            fatalSuccess = success;
        },
        1000,
        3);
    assert(fatalId == 0);
    assert(fatalCallbacks == 1);
    assert(!fatalSuccess);
    assert(lcmm->getLastSendResult() == MAC_SEND_RADIO_ERROR);
    assert(!lcmm->isSending());

    // Start one real reliable LCMM transmission and deliberately never inject
    // its link ACK. This drives timeoutHandler() and verifies that a transient
    // denial during a retry preserves the pending send/retry budget.
    int retryCallbacks = 0;
    bool retrySuccess = true;
    const uint16_t id = lcmm->sendPacketSingle(
        true,
        2,
        payload,
        sizeof(payload),
        [&](uint16_t, bool success) {
            ++retryCallbacks;
            retrySuccess = success;
        },
        100,
        3);
    assert(id != 0);
    assert(lcmm->isSending());

    hostsim::TxFrame frame{};
    assert(hostsim::pop_tx(frame));
    hostsim::phy_done(frame.token);

    // Advance beyond DATA airtime + ACK timeout. The first retry is denied by
    // duty policy; LCMM should remain pending and should not notify the caller.
    hostsim::set_time_ms(10'000);
    hostsim::set_next_send_result(MAC_SEND_DUTY_CYCLE, 100);
    lcmm->loop();
    assert(lcmm->isSending());
    assert(retryCallbacks == 0);
    assert(lcmm->getLastSendResult() == MAC_SEND_DUTY_CYCLE);
    assert(MAC::getInstance()->getTransmitWaitMs() == 100);

    // When that wait expires, make the next retry fail fatally. It must now
    // terminate exactly once rather than preserving the pending state forever.
    hostsim::set_time_ms(10'101);
    hostsim::set_next_send_result(MAC_SEND_RADIO_ERROR, 0);
    lcmm->loop();
    assert(!lcmm->isSending());
    assert(retryCallbacks == 1);
    assert(!retrySuccess);
    assert(lcmm->getLastSendResult() == MAC_SEND_RADIO_ERROR);

    // Retry timing is based on unsigned millis() deltas. Verify that a reliable
    // send waiting for its link ACK terminates exactly once across the 32-bit
    // Arduino clock wrap instead of hanging for another 49 days.
    hostsim::set_time_ms(0xfffffff0ULL);
    int wrapCallbacks = 0;
    bool wrapSuccess = true;
    const uint16_t wrapId = lcmm->sendPacketSingle(
        true,
        2,
        payload,
        sizeof(payload),
        [&](uint16_t, bool success) {
            ++wrapCallbacks;
            wrapSuccess = success;
        },
        100,
        1);
    assert(wrapId != 0);
    assert(hostsim::pop_tx(frame));
    hostsim::phy_done(frame.token);
    hostsim::set_time_ms(0x1'0000'0000ULL + 10'000ULL);
    lcmm->loop();
    assert(!lcmm->isSending());
    assert(wrapCallbacks == 1);
    assert(!wrapSuccess);

    // Oversized LCMM payloads must fail synchronously and leave no hidden
    // sender state behind. This is an application-visible configuration error,
    // not something that retries can repair.
    {
        bool callbackCalled = false;
        bool callbackSuccess = true;
        unsigned char oversized[DATASIZE_LCMM + 1u] = {};
        const uint16_t oversizedId = LCMM::getInstance()->sendPacketSingle(
            true,
            2,
            oversized,
            static_cast<uint8_t>(sizeof(oversized)),
            [&callbackCalled, &callbackSuccess](uint16_t, bool success) {
                callbackCalled = true;
                callbackSuccess = success;
            },
            3000,
            3);
        assert(oversizedId == 0);
        assert(callbackCalled);
        assert(!callbackSuccess);
        assert(LCMM::getInstance()->getLastSendResult() == MAC_SEND_TOO_LARGE);
        assert(!LCMM::getInstance()->isSending());
    }

    return 0;
}
