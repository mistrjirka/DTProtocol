#include "Arduino.h"

#include <cstdarg>
#include <random>

HostSerial Serial;

namespace {
uint64_t g_now_ms = 0;
std::mt19937_64 g_rng(1);
}

uint32_t millis() {
    return static_cast<uint32_t>(g_now_ms & 0xffffffffULL);
}

void delay(uint32_t ms) {
    g_now_ms += ms;
}

void randomSeed(uint64_t seed) {
    g_rng.seed(seed);
}

long random(long min_value, long max_value) {
    if (max_value <= min_value) return min_value;
    std::uniform_int_distribution<long> dist(min_value, max_value - 1);
    return dist(g_rng);
}

long random(long max_value) {
    return random(0, max_value);
}

int host_debug_printf(const char *, ...) {
    return 0;
}
