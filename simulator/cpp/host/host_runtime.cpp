#include "host_runtime.h"
#include "mac.h"

#include <cstdarg>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <random>
#include <utility>

HostSerial Serial;

namespace {
uint64_t g_now_ms = 0;
std::mt19937_64 g_rng(1);
std::deque<hostsim::TxFrame> g_tx_queue;
uint64_t g_next_tx_token = 1;
}

uint32_t millis() {
    return static_cast<uint32_t>(g_now_ms & 0xffffffffULL);
}

void delay(uint32_t ms) { g_now_ms += ms; }

void randomSeed(uint64_t seed) { g_rng.seed(seed); }

long random(long min_value, long max_value) {
    if (max_value <= min_value) return min_value;
    std::uniform_int_distribution<long> dist(min_value, max_value - 1);
    return dist(g_rng);
}

long random(long max_value) { return random(0, max_value); }

int host_debug_printf(const char *, ...) { return 0; }

MAC *MAC::instance_ = nullptr;
State MAC::state_ = RECEIVING;

MAC::MAC(uint16_t id) : id_(id) {}

void MAC::initialize(int id) {
    if (!instance_) instance_ = new MAC(static_cast<uint16_t>(id));
}

MAC *MAC::getInstance() { return instance_; }

void MAC::setRXCallback(PacketReceivedCallback callback) {
    rx_callback_ = std::move(callback);
}

void MAC::setRXAlienCallback(PacketReceivedCallback callback) {
    rx_alien_callback_ = std::move(callback);
}

uint8_t MAC::sendData(uint16_t target, unsigned char *data,
                      uint8_t size, uint32_t) {
    if (state_ == SENDING) return 5;
    active_tx_token_ = hostsim::enqueue_tx(target, data, size);
    state_ = SENDING;
    return 0;
}

uint32_t MAC::getTransmitWaitMs() const { return 0; }

void MAC::loop() {}

uint32_t MAC::random() {
    return static_cast<uint32_t>(::random(0, 65000));
}

void MAC::setMode(State state, bool force) {
    if (force || state_ != state) state_ = state;
}

State MAC::getMode() { return state_; }
uint16_t MAC::getId() { return id_; }

void MAC::setTransmitDone(TransmitDone callback) {
    transmit_done_ = std::move(callback);
}

bool MAC::hostInject(uint16_t sender, uint16_t target,
                     const std::vector<uint8_t> &payload) {
    if (state_ != RECEIVING) return false;

    const size_t total = sizeof(MACHeader) + payload.size();
    auto *packet = static_cast<MACPacket *>(std::malloc(total));
    if (!packet) return false;
    packet->crc32 = 0x51A7C0DEu;
    packet->sender = sender;
    packet->target = target;
    if (!payload.empty()) std::memcpy(packet->data, payload.data(), payload.size());

    auto cb = (target == 0 || target == id_) ? rx_callback_ : rx_alien_callback_;
    if (!cb) {
        std::free(packet);
        return false;
    }
    cb(packet, static_cast<uint16_t>(total), packet->crc32);
    return true;
}

void MAC::hostPhyDone(uint64_t token) {
    if (state_ != SENDING || token != active_tx_token_) return;
    state_ = RECEIVING;
    active_tx_token_ = 0;
    if (transmit_done_) transmit_done_();
}

namespace hostsim {

void reset(uint16_t, uint64_t seed) {
    g_now_ms = 0;
    g_rng.seed(seed);
    g_tx_queue.clear();
    g_next_tx_token = 1;
}

void set_time_ms(uint64_t now_ms) { g_now_ms = now_ms; }
uint64_t time_ms() { return g_now_ms; }

uint64_t enqueue_tx(uint16_t target, const unsigned char *data, uint8_t size) {
    TxFrame frame;
    frame.token = g_next_tx_token++;
    frame.target = target;
    frame.payload.assign(data, data + size);
    g_tx_queue.push_back(std::move(frame));
    return g_tx_queue.back().token;
}

bool pop_tx(TxFrame &frame) {
    if (g_tx_queue.empty()) return false;
    frame = std::move(g_tx_queue.front());
    g_tx_queue.pop_front();
    return true;
}

bool inject_frame(uint16_t sender, uint16_t target,
                  const std::vector<uint8_t> &payload) {
    return MAC::getInstance() && MAC::getInstance()->hostInject(sender, target, payload);
}

void phy_done(uint64_t token) {
    if (MAC::getInstance()) MAC::getInstance()->hostPhyDone(token);
}

} // namespace hostsim
