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

bool pop_tx(TxFrame &frame);
bool inject_frame(uint16_t sender, uint16_t target,
                  const std::vector<uint8_t> &payload);
void phy_done(uint64_t token);
uint64_t enqueue_tx(uint16_t target, const unsigned char *data, uint8_t size);

} // namespace hostsim
