#ifndef MAC_LAYER_H
#define MAC_LAYER_H

#include <Arduino.h>
#include <RadioLib.h>
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
  // 433.05-434.79 MHz, 10% duty-cycle fallback.
  EU433,
  // 868.0-868.6 MHz, conventional 868.1/868.3/868.5 channels, 1% fallback.
  EU868,
  // Czech/EU g6 high-duty profile: the whole 869.4-869.65 MHz band may be one
  // high-speed-data channel. 869.525 MHz/BW125 fits inside it; fallback duty is
  // 10%. Kept explicit so applications choose the regulatory trade-off.
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

  // Backward-compatible initializer. v2 treats it as the EU433 profile.
  static void initialize(
      SX1262 &loramodule,
      int id,
      int default_channel = 0,
      int default_spreading_factor = DEFAULT_SPREADING_FACTOR,
      float default_bandwidth = DEFAULT_BANDWIDTH,
      int squelch = DEFAULT_SQUELCH,
      int default_power = DEFAULT_POWER,
      int default_coding_rate = DEFAULT_CODING_RATE);

  static void initialize(
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
  uint8_t getFallbackDutyCyclePercent() const { return dutyCyclePercent; }

  // Time until MAC policy permits another send. This is deliberately
  // non-blocking: callers can keep servicing routing/RX while duty cycle or
  // randomized carrier-sense backoff is active.
  uint32_t getTransmitWaitMs() const;

  void handlePacket();
  uint8_t sendData(uint16_t target, unsigned char *data,
                   uint8_t size, uint32_t timeout = 5000);
  void loop();
  uint32_t random();
  void setMode(State state, bool force = true);
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

  // Largest current profile has 13 channels. Keep fixed storage to avoid heap
  // allocation in radio calibration.
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
  void setFrequencyAndListen(uint16_t channel);
  void setFrequency(uint16_t channel);
  bool transmissionAuthorized();
  bool waitForTransmissionAuthorization(uint32_t timeout);
  void calibrateBasedOnLastPacket();
  bool validChannel(uint16_t channel) const;
  bool configureRadio(int default_spreading_factor, float default_bandwidth,
                      int default_power, int default_coding_rate);
  static bool deadlinePending(uint32_t now, uint32_t deadline);
  void startCarrierBackoff();
  void accountDutyCycle(uint8_t packetLength);
};

#endif // MAC_LAYER_H
