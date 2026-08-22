#include "Arduino.h"

#include <cstdarg>
#include <random>

HostSerial Serial;

namespace {
uint64_t g_now_us = 0;
std::mt19937_64 g_rng(1);
}

uint32_t millis() {
    return static_cast<uint32_t>((g_now_us / 1000ULL) & 0xffffffffULL);
}

uint64_t micros() {
    return g_now_us;
}

void delay(uint32_t ms) {
    g_now_us += static_cast<uint64_t>(ms) * 1000ULL;
}

void delayMicroseconds(uint32_t us) {
    g_now_us += us;
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
