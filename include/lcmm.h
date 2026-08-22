#ifndef LCMM_LAYER_H
#define LCMM_LAYER_H
#include <cstdint>
#include "generalsettings.h"
#include "mac.h"
#include <functional>
#include <vector>
#include <cstring>
#include <stdio.h>
#include <stdlib.h>

using namespace std;

#define PACKET_TYPE_DATA_NOACK 0
#define PACKET_TYPE_DATA_ACK 1
// Values 2, 3 and 5-7 are reserved by abandoned early experiments.
#define PACKET_TYPE_ACK 4

typedef struct __attribute__((packed))
{
  MACHeader mac;
  uint8_t type;
  unsigned char data[];
} LCMMPacketUknownTypeReceive;

typedef struct __attribute__((packed))
{
  MACHeader mac;
  uint8_t type;
  uint16_t packetIds[];
} LCMMPacketResponseReceive;

typedef struct __attribute__((packed))
{
  MACHeader mac;
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketDataReceive;

typedef struct __attribute__((packed))
{
  MACHeader mac;
  uint8_t type;
  uint16_t id;
} LCMMDataHeader;

typedef struct __attribute__((packed))
{
  uint8_t type;
  uint16_t packetIds[];
} LCMMPacketResponse;

typedef struct __attribute__((packed))
{
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketData;

class LCMM
{
public:
  static void ReceivedPacket(int size);
  using DataReceivedCallback =
      function<void(LCMMPacketDataReceive *data, uint32_t size)>;
  using AcknowledgmentCallback =
      function<void(uint16_t packetId, bool success)>;

  struct ACKWaitingSingle
  {
    AcknowledgmentCallback callback;
    LCMMPacketData *packet;
    int timeout;
    int timeLeft;
    uint16_t target;
    uint8_t size;
    uint16_t id;
    uint8_t attemptsLeft;
  };

  static LCMM *getInstance();
  static void initialize(DataReceivedCallback dataReceived,
                         AcknowledgmentCallback TransmissionComplete);

  uint16_t sendPacketSingle(bool needACK, uint16_t target, unsigned char *data,
                            uint8_t size, AcknowledgmentCallback callback,
                            uint32_t timeout = 3000, uint8_t attempts = 3);
  void loop();
  bool isSending();

  // Why the latest sendPacketSingle() call returned 0. This lets the routing
  // layer distinguish a temporary carrier/duty backoff from a real radio or
  // allocation failure without changing the existing packet-ID return API.
  uint8_t getLastSendResult() const { return lastSendResult; }

private:
  static bool sending;
  static void ReceivePacket(MACPacket *packet, uint16_t size, uint32_t correct);
  static ACKWaitingSingle ackWaitingSingle;
  static bool waitingForACKSingle;
  static uint16_t packetId;
  static LCMM *lcmm;
  static bool timeoutHandler();

  static LCMMPacketDataReceive *afterCallbackSent_packet;
  static uint16_t afterCallbackSent_size;
  static void afterCallbackSent();

  static uint16_t noAckId;
  static AcknowledgmentCallback noAckAcknowledgmentCallback;
  static void noAckTransmitDone();

  uint32_t lastTick;
  uint8_t lastSendResult;

  LCMM(DataReceivedCallback dataReceived,
       AcknowledgmentCallback TransmissionComplete);
  ~LCMM();

  LCMM(const LCMM &) = delete;
  LCMM &operator=(const LCMM &) = delete;

  DataReceivedCallback dataReceived;
  AcknowledgmentCallback transmissionComplete;

  void handleDataNoACK(LCMMPacketDataReceive *data, uint16_t size);
  void handleDataACK(LCMMPacketDataReceive *data, uint16_t size);
  void handleACK(LCMMPacketResponseReceive *data, uint16_t size);
  void clearSendingPacket();
  ACKWaitingSingle prepareAckWaitingSingle(
      AcknowledgmentCallback callback, uint32_t timeout, LCMMPacketData *packet,
      uint8_t attemptsLeft, uint16_t target, uint8_t size,
      uint32_t timeBeforeSending, uint32_t timeAfterSending);
};

#endif // LCMM_LAYER_H
