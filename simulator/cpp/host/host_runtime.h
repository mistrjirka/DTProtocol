#pragma once

#include <cstdint>
#include <vector>

namespace hostsim {

struct TxFrame {
    uint64_t token;
    uint16_t target;
    std::vector<uint8_t> payload;
};

void reset(uint16_t node_id, uint64_t seed = 1);
void set_time_ms(uint64_t now_ms);
uint64_t time_ms();

// Configure the same conservative per-packet duty policy used by production
// MAC. initial_wait_ms preserves RF history across a simulated MCU reboot.
void set_duty_cycle_percent(float percent, uint32_t initial_wait_ms = 0);

// Force the next MAC::sendData() call to return a specific MACSendResult.
// wait_ms models a non-blocking carrier/duty policy deadline for tests.
void set_next_send_result(uint8_t result, uint32_t wait_ms = 0);

bool pop_tx(TxFrame &frame);
bool inject_frame(uint16_t sender, uint16_t target,
                  const std::vector<uint8_t> &payload);
void phy_done(uint64_t token);
uint64_t enqueue_tx(uint16_t target, const unsigned char *data, uint8_t size);

} // namespace hostsim
