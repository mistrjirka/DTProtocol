#ifndef MAC_LAYER_H
#define MAC_LAYER_H

#include <Arduino.h>
#include <RadioLib.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include "generalsettings.h"
#include "mathextension.h"

#if defined(ESP8266) && !defined(ESP_PLATFORM)
#define ESP_PLATFORM
#endif
#if defined(ESP32) && !defined(ESP_PLATFORM)
#define ESP_PLATFORM
#endif
#ifdef ESP_PLATFORM
#define RAM_ATTR ICACHE_RAM_ATTR
#else
#define RAM_ATTR
#endif

#define DEFAULT_SPREADING_FACTOR 9
#define DEFAULT_PREAMBLE_LENGTH 8
#define DEFAULT_SYNC_WORD RADIOLIB_SX126X_SYNC_WORD_PRIVATE
#define DEFAULT_BANDWIDTH 125.0f
#define DEFAULT_CODING_RATE 7
#define DEFAULT_SQUELCH 15
#define DEFAULT_POWER 10
#define NUMBER_OF_MEASUREMENTS 10
#define NUMBER_OF_MEASUREMENTS_LBT 3
#define TIME_BETWEENMEASUREMENTS 10
#define DISCRIMINATE_MEASURMENTS 2

enum State
{
  IDLE,
  SENDING,
  RECEIVING,
  SLEEPING
};

enum class MACRegion : uint8_t
{
  EU433,
  EU868,
  EU869_HIGH_DUTY,
};

enum MACSendResult : uint8_t
{
  MAC_SEND_OK = 0,
  MAC_SEND_CHANNEL_BUSY_TIMEOUT = 1,
  MAC_SEND_ALLOC_FAILED = 2,
  MAC_SEND_TOO_LARGE = 3,
  MAC_SEND_RADIO_ERROR = 4,
  MAC_SEND_BUSY = 5,
  MAC_SEND_DUTY_CYCLE = 6,
};

typedef struct __attribute__((packed))
{
  uint32_t crc32;
  uint16_t sender;
  uint16_t target;
  unsigned char data[];
} MACPacket;

typedef struct __attribute__((packed))
{
  uint32_t crc32;
  uint16_t sender;
  uint16_t target;
} MACHeader;

class MAC
{
public:
  using PacketReceivedCallback =
      std::function<void(MACPacket *packet, uint16_t size, uint32_t crcCalculated)>;
  using TransmitDone = std::function<void(void)>;

  static MAC *getInstance();

  static bool initialize(
      SX1262 &loramodule,
      int id,
      int default_channel = 0,
      int default_spreading_factor = DEFAULT_SPREADING_FACTOR,
      float default_bandwidth = DEFAULT_BANDWIDTH,
      int squelch = DEFAULT_SQUELCH,
      int default_power = DEFAULT_POWER,
      int default_coding_rate = DEFAULT_CODING_RATE);

  static bool initialize(
      SX1262 &loramodule,
      int id,
      MACRegion region,
      int default_channel,
      int default_spreading_factor = DEFAULT_SPREADING_FACTOR,
      float default_bandwidth = DEFAULT_BANDWIDTH,
      int squelch = DEFAULT_SQUELCH,
      int default_power = DEFAULT_POWER,
      int default_coding_rate = DEFAULT_CODING_RATE);

  int LORANoiseFloorCalibrate(int channel, bool save = true);
  void LORANoiseCalibrateAllChannels(bool save = true);
  void setRXCallback(PacketReceivedCallback callback);
  void setRXAlienCallback(PacketReceivedCallback callback);
  int getNoiseFloorOfChannel(uint8_t channel_num);
  uint8_t getNumberOfChannels();
  MACRegion getRegion() const { return region; }

  // Region selection configures frequencies/power only. Regulatory duty-cycle
  // throttling is intentionally opt-in because the strict fallback limits make
  // this reliable multi-hop protocol impractically slow. 0 disables it.
  uint8_t getDutyCycleLimitPercent() const { return dutyCyclePercent; }
  uint8_t getFallbackDutyCyclePercent() const { return dutyCyclePercent; }
  uint8_t recommendedRegionalDutyCyclePercent() const
  {
    return region == MACRegion::EU868 ? 1u : 10u;
  }
  void setDutyCycleLimitPercent(uint8_t percent)
  {
    dutyCyclePercent = std::min<uint8_t>(percent, 100u);
    if (dutyCyclePercent == 0)
      dutyCycleUntil = 0;
  }

  // RSSI energy sensing is the default. SX126x CAD is a LoRa correlator whose
  // detection probability falls near sensitivity and which only sees matching
  // LoRa waveforms; enable it only as an additional check after RSSI says free.
  void setCadCarrierSenseEnabled(bool enabled) { cadCarrierSenseEnabled = enabled; }
  bool isCadCarrierSenseEnabled() const { return cadCarrierSenseEnabled; }

  uint32_t getTransmitWaitMs() const;
  uint32_t estimateFrameAirtimeMs(uint16_t frameBytes) const;
  bool isReady() const { return ready; }
  int16_t getLastRadioError() const { return lastRadioError; }

  struct Diagnostics
  {
    uint32_t radioCommandErrors = 0;
    uint32_t rxTooShort = 0;
    uint32_t rxAllocationFailures = 0;
    uint32_t rxReadErrors = 0;
  };
  const Diagnostics &getDiagnostics() const { return diagnostics; }

  // Helper for applications that explicitly enable a duty limit and want to
  // derive a liveness timeout. DTPK does not call this automatically.
  uint32_t recommendedNeighborExpiryMs(
      uint32_t baseMs,
      uint32_t maxHelloGapMs,
      uint32_t schedulerMarginMs = 1000) const
  {
    if (dutyCyclePercent == 0)
      return baseMs;

    const float airtimeMs = MathExtension.timeOnAir(
        MAX_PACKET_SIZE,
        DEFAULT_PREAMBLE_LENGTH,
        static_cast<uint8_t>(spreading_factor),
        bandwidth,
        static_cast<uint8_t>(coding_rate));
    if (!(airtimeMs > 0.0f) || !std::isfinite(airtimeMs))
      return baseMs;

    const double offTimeMs =
        static_cast<double>(airtimeMs) *
        (100.0 / static_cast<double>(dutyCyclePercent) - 1.0);
    const double required =
        offTimeMs + static_cast<double>(maxHelloGapMs) +
        static_cast<double>(schedulerMarginMs);
    const uint32_t bounded = static_cast<uint32_t>(std::min<double>(
        std::ceil(required), static_cast<double>(0x7fffffffu)));
    return std::max(baseMs, bounded);
  }

  void handlePacket();
  uint8_t sendData(uint16_t target, unsigned char *data,
                   uint8_t size, uint32_t timeout = 5000);
  void loop();
  uint32_t random();
  bool setMode(State state, bool force = true);
  State getMode();
  uint16_t getId();
  SX1262 &module;
  void setTransmitDone(TransmitDone callback);

private:
  static volatile bool operationDone;
  static MAC *mac;
  static State state;

  static const double EU433_CHANNELS[];
  static const double EU868_CHANNELS[];
  static const double EU869_HIGH_DUTY_CHANNELS[];
  static const uint8_t EU433_CHANNEL_COUNT;
  static const uint8_t EU868_CHANNEL_COUNT;
  static const uint8_t EU869_HIGH_DUTY_CHANNEL_COUNT;

  const double *channels;
  uint8_t channelCount;
  MACRegion region;
  int maxConductedPowerDbm;
  uint8_t dutyCyclePercent;
  bool cadCarrierSenseEnabled;

  int noiseFloor[13];
  uint16_t id;
  int channel;
  int spreading_factor;
  float bandwidth;
  int squelch;
  int power;
  int coding_rate;
  double calibratedFrequency;
  TransmitDone transmitDone;
  PacketReceivedCallback RXCallback;
  PacketReceivedCallback RXAlienCallback;

  uint32_t carrierBackoffUntil;
  uint32_t dutyCycleUntil;
  bool ready;
  int16_t lastRadioError;
  Diagnostics diagnostics;

  MAC(
      SX1262 &loramodule,
      int id,
      MACRegion region,
      int default_channel,
      int default_spreading_factor,
      float default_bandwidth,
      int squelch,
      int default_power,
      int default_coding_rate);
  ~MAC();
  MAC(const MAC &) = delete;
  MAC &operator=(const MAC &) = delete;

  MACPacket *createPacket(uint16_t sender, uint16_t target,
                          unsigned char *data, uint8_t size);
  static void setFlag(void);
  static bool irqPending();
  bool setFrequencyAndListen(uint16_t channel);
  bool setFrequency(uint16_t channel);
  bool transmissionAuthorized();
  bool waitForTransmissionAuthorization(uint32_t timeout);
  void calibrateBasedOnLastPacket();
  bool validChannel(uint16_t channel) const;
  bool configureRadio(int default_spreading_factor, float default_bandwidth,
                      int default_power, int default_coding_rate);
  bool recordRadioStatus(int status, const char *operation);
  static bool deadlinePending(uint32_t now, uint32_t deadline);
  void startCarrierBackoff();
  void accountDutyCycle(uint8_t packetLength);
};

#endif // MAC_LAYER_H
