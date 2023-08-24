#ifndef MAC_LAYER_H
#include <Arduino.h>
#include "generalsettings.h"
#include <cstdint>
#include <functional>
#include <RadioLib.h>
#include "mathextension.h"
#include <stdexcept>

// IRQ masks
#define IRQ_TX_DONE_MASK           0x08
#define IRQ_PAYLOAD_CRC_ERROR_MASK 0x20
#define IRQ_RX_DONE_MASK           0x40
#define IRQ_CAD_DONE_MASK          0x04
#define IRQ_CAD_DETECTED_MASK      0x01

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

#define MAC_LAYER_H
#define NUM_OF_CHANNELS 15
#define DEFAULT_CHANNEL 10
#define DEFAULT_SPREADING_FACTOR 9
#define DEFAULT_PREAMBLE_LENGTH 8
#define DEFAULT_SYNC_WORD RADIOLIB_SX126X_SYNC_WORD_PRIVATE
#define DEFAULT_BANDWIDTH 125.0
#define DEFAULT_CODING_RATE 7
#define DEFAULT_SQUELCH 15
#define DEFAULT_POWER 10 // dBm
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

typedef struct
{
  uint32_t crc32;
  uint16_t sender;
  uint16_t target;
  unsigned char data[];
} MACPacket;

typedef struct
{
  uint32_t crc32;
  uint16_t sender;
  uint16_t target;
} MACHeader;

class MAC
{
public:
  // Callback function type definition
  using PacketReceivedCallback =
      std::function<void(MACPacket *packet, uint16_t size, uint32_t crcCalculated)>;

  int LORANoiseFloorCalibrate(int channel, bool save = true);
  void LORANoiseCalibrateAllChannels(bool save /*= true*/);
  // Function to access the singleton instance
  static MAC *getInstance();
  static void ChannelActivity(bool signal);
  void setRXCallback(PacketReceivedCallback callback);

  int getNoiseFloorOfChannel(uint8_t channel_num);
  uint8_t getNumberOfChannels();



  static void initialize(SX1262 loramodule, int id,
             int default_channel = DEFAULT_CHANNEL,
             int default_spreading_factor = DEFAULT_SPREADING_FACTOR,
             float default_bandwidth = DEFAULT_SPREADING_FACTOR,
             int squelch = DEFAULT_SQUELCH,
             int default_power = DEFAULT_POWER,
             int default_coding_rate = DEFAULT_CODING_RATE);

  // Function to handle incoming packets or events
  void handlePacket();

  // Function to send packets to the next layer (DTP)
  uint8_t sendData(uint16_t target, unsigned char *data,
                   uint8_t size, uint32_t timeout = 5000);
  void loop();

  void setMode(State state, bool force = true);
  State getMode();
  SX1262 module;

  // Other member functions as needed
private:
  static bool operationDone;
  static MAC *mac;
  static State state;
  double channels[NUM_OF_CHANNELS] = {433.05, 433.175, 433.3, 433.425,
                                      433.55, 433.675, 433.8, 433.925,
                                      434.05, 434.175, 434.3, 434.425,
                                      434.55, 434.675, 434.8};

  int noiseFloor[NUM_OF_CHANNELS];
  int id;
  int channel;
  int spreading_factor;
  int bandwidth;
  int squelch;
  int power;
  int coding_rate;
  bool readyToReceive;
  bool packetTransmitting;
  // Private constructor

  MAC(SX1262 loramodule, int id,
      int default_channel = DEFAULT_CHANNEL,
      int default_spreading_factor = DEFAULT_SPREADING_FACTOR,
      float default_bandwidth = DEFAULT_BANDWIDTH, int squelch = DEFAULT_SQUELCH,
      int default_power = DEFAULT_POWER,
      int default_coding_rate = DEFAULT_CODING_RATE);

  // Private destructor
  ~MAC();

  // Private copy constructor and assignment operator to prevent copying
  MAC(const MAC &) = delete;
  

  MAC &operator=(const MAC &) = delete;
  MACPacket *createPacket(uint16_t sender, uint16_t target, unsigned char *data,
                          uint8_t size);

  static void setFlag(void);
  
  void setFrequencyAndListen(uint16_t);

  bool transmissionAuthorized();
  bool waitForTransmissionAuthorization(uint32_t timeout);

  // Private member variables for MAC layer
  PacketReceivedCallback RXCallback;

  // Private helper functions as needed
};

#endif // MAC_LAYER_H