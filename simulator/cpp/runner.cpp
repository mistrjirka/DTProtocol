#include "host/host_runtime.h"
#include <DTPK.h>
#include <mac.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::string hex_encode(const std::vector<uint8_t> &bytes) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (uint8_t b : bytes) out << std::setw(2) << static_cast<unsigned>(b);
    return out.str();
}

std::string hex_encode(const unsigned char *data, size_t size) {
    return hex_encode(std::vector<uint8_t>(data, data + size));
}

std::vector<uint8_t> hex_decode(const std::string &text) {
    if (text == "-" || text.empty()) return {};
    if (text.size() % 2) throw std::runtime_error("odd hex length");
    std::vector<uint8_t> result;
    result.reserve(text.size() / 2);
    for (size_t i = 0; i < text.size(); i += 2) {
        result.push_back(static_cast<uint8_t>(std::stoul(text.substr(i, 2), nullptr, 16)));
    }
    return result;
}

void flush_tx() {
    hostsim::TxFrame frame;
    while (hostsim::pop_tx(frame)) {
        std::cout << "TX " << frame.token << ' ' << frame.target << ' '
                  << (frame.payload.empty() ? "-" : hex_encode(frame.payload)) << '\n';
    }
}

void done() {
    flush_tx();
    std::cout << "DONE\n" << std::flush;
}

void service_protocol_once(bool initialized) {
    if (initialized)
        DTPK::getInstance()->loop();
}

} // namespace

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::cout << "READY\n" << std::flush;

    std::string line;
    bool initialized = false;
    uint16_t node_id = 0;

    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::istringstream in(line);
        std::string command;
        in >> command;

        try {
            if (command == "INIT") {
                unsigned id = 0, k_limit = 20, origin_sequence = 0;
                uint64_t seed = 1;
                double duty_cycle_percent = 0.0;
                uint32_t initial_duty_wait_ms = 0;
                unsigned mobile_hint = 0;
                in >> id >> k_limit >> seed;
                if (!(in >> origin_sequence)) {
                    origin_sequence = static_cast<unsigned>(seed & 0xffffu);
                    if (origin_sequence == 0) origin_sequence = 1;
                    in.clear();
                } else {
                    // Newer adapters append these fields. Older command lines
                    // remain valid and default to an unconstrained RF profile.
                    if (!(in >> duty_cycle_percent)) {
                        duty_cycle_percent = 0.0;
                        in.clear();
                    }
                    if (!(in >> initial_duty_wait_ms)) {
                        initial_duty_wait_ms = 0;
                        in.clear();
                    } else if (!(in >> mobile_hint)) {
                        mobile_hint = 0;
                        in.clear();
                    }
                }
                node_id = static_cast<uint16_t>(id);
                hostsim::reset(node_id, seed);
                MAC::initialize(node_id);
                hostsim::set_duty_cycle_percent(
                    static_cast<float>(duty_cycle_percent),
                    initial_duty_wait_ms);
                DTPK::initialize(
                    static_cast<uint8_t>(k_limit),
                    static_cast<uint16_t>(origin_sequence == 0 ? 1 : origin_sequence),
                    mobile_hint != 0);
                DTPK::getInstance()->setPacketReceivedCallback(
                    [](DTPKPacketGeneric *packet, uint16_t size) {
                        const size_t header = sizeof(DTPKPacketGeneric);
                        const size_t payload_size = size > header ? size - header : 0;
                        std::cout << "APP_RX " << packet->originalSender << ' '
                                  << packet->finalTarget << ' ' << packet->id << ' '
                                  << (payload_size ? hex_encode(packet->data, payload_size) : "-")
                                  << '\n';
                    });
                initialized = true;
                done();
            } else if (command == "TICK") {
                uint64_t now = 0;
                in >> now;
                hostsim::set_time_ms(now);
                service_protocol_once(initialized);
                done();
            } else if (command == "INJECT") {
                uint64_t now = 0;
                unsigned sender = 0, target = 0;
                std::string hex;
                in >> now >> sender >> target >> hex;
                hostsim::set_time_ms(now);
                bool accepted = hostsim::inject_frame(
                    static_cast<uint16_t>(sender), static_cast<uint16_t>(target), hex_decode(hex));
                std::cout << "INJECTED " << (accepted ? 1 : 0) << '\n';
                // Real firmware returns to its main loop immediately after the
                // radio callback. Do the same here so packet processing and
                // control replies are not quantized by CppNetwork::tick_ms.
                if (accepted)
                    service_protocol_once(initialized);
                done();
            } else if (command == "PHYDONE") {
                uint64_t now = 0, token = 0;
                in >> now >> token;
                hostsim::set_time_ms(now);
                hostsim::phy_done(token);
                // TX-done callbacks can hand an ACKed DATA packet upward or
                // release LCMM. Service that work in the same firmware turn.
                service_protocol_once(initialized);
                done();
            } else if (command == "SEND") {
                uint64_t now = 0;
                unsigned target = 0;
                int timeout = 10000;
                unsigned ack = 1;
                std::string hex;
                in >> now >> target >> timeout >> ack >> hex;
                hostsim::set_time_ms(now);
                auto payload = hex_decode(hex);
                uint16_t id = DTPK::getInstance()->sendPacket(
                    static_cast<uint16_t>(target), payload.data(), payload.size(),
                    static_cast<int16_t>(timeout), ack != 0,
                    [](uint8_t result, uint16_t ping) {
                        std::cout << "APP_ACK " << static_cast<unsigned>(result)
                                  << ' ' << ping << '\n';
                    });
                std::cout << "SENDID " << id << '\n';
                // Application code and protocol loop run back-to-back on the
                // MCU; do not inject an artificial <=50 ms send-start delay.
                service_protocol_once(initialized);
                done();
            } else if (command == "ROUTES") {
                uint64_t now = 0;
                in >> now;
                hostsim::set_time_ms(now);
                auto routes = DTPK::getInstance()->getNeighbours();
                std::sort(routes.begin(), routes.end(),
                          [](const NeighborRecord &a, const NeighborRecord &b) {
                              return a.id < b.id;
                          });
                std::cout << "ROUTES " << routes.size();
                for (const auto &r : routes) {
                    std::cout << ' ' << r.id << ':' << r.from << ':'
                              << static_cast<unsigned>(r.distance);
                }
                std::cout << '\n';
                done();
            } else if (command == "QUIT") {
                std::cout << "DONE\n" << std::flush;
                break;
            } else {
                std::cout << "ERR unknown-command\n";
                done();
            }
        } catch (const std::exception &e) {
            std::cout << "ERR " << e.what() << '\n';
            done();
        }
    }

    return 0;
}
