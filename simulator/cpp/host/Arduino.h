#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <type_traits>

#define F(x) x
using boolean = bool;

class String {
public:
    String() = default;
    String(const char *value) : value_(value ? value : "") {}
    String(const std::string &value) : value_(value) {}
    String(const String &) = default;
    String &operator=(const String &) = default;

    template <typename T,
              typename std::enable_if<std::is_arithmetic<T>::value, int>::type = 0>
    String(T value) : value_(std::to_string(value)) {}

    const char *c_str() const { return value_.c_str(); }

    friend String operator+(const String &a, const String &b) {
        return String(a.value_ + b.value_);
    }
    friend String operator+(const char *a, const String &b) {
        return String(a) + b;
    }
    friend String operator+(const String &a, const char *b) {
        return a + String(b);
    }

private:
    std::string value_;
};

template <typename T,
          typename std::enable_if<std::is_arithmetic<T>::value, int>::type = 0>
inline String operator+(T a, const String &b) {
    return String(a) + b;
}

template <typename T,
          typename std::enable_if<std::is_arithmetic<T>::value, int>::type = 0>
inline String operator+(const String &a, T b) {
    return a + String(b);
}

struct HostSerial {
    template <typename T> void print(const T &) {}
    template <typename T> void println(const T &) {}
    void println() {}
};

extern HostSerial Serial;

uint32_t millis();
void delay(uint32_t ms);
void randomSeed(uint64_t seed);
long random(long min_value, long max_value);
long random(long max_value);

int host_debug_printf(const char *fmt, ...);

#ifdef HOST_SIM
// Keep production debug output from corrupting the runner's line protocol.
#define printf host_debug_printf
#endif
