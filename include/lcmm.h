#ifndef LCMM_LAYER_H
#define LCMM_LAYER_H
#include <cstdint>
// Include necessary headers
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
#define PACKET_TYPE_DATA_CLUSTER_ACK 2
//#define PACKET_TYPE_DATA_SET 3
#define PACKET_TYPE_ACK 4
#define PACKET_TYPE_PACKET_NEGOTIATION 5
#define PACKET_TYPE_PACKET_NEGOTIATION_REFUSED 6
#define PACKET_TYPE_PACKET_NEGOTIATION_ACCEPTED 7

typedef struct  __attribute__((packed)) {
  MACHeader mac;
  uint8_t type;
  unsigned char data[];
} LCMMPacketUknownTypeRecieve;


typedef struct  __attribute__((packed)) {
  MACHeader mac;
  uint8_t type;
  uint16_t packetIds[];
} LCMMPacketResponseRecieve;

typedef struct  __attribute__((packed)) {
  MACHeader mac;
  uint8_t type;
  uint16_t id;
  uint8_t ackInterval; // amount of time after which ack is expected from the point the ack to the transmission has been recieved
  uint16_t packetIdStart; // number of packets being send
  uint16_t packetIdEnd; // number of packets to be sent
  unsigned char data[];
} LCMMPacketNegotiationRecieve;

typedef struct  __attribute__((packed)) {
  MACHeader mac;
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketNegotiationResponseRecieve;

typedef struct __attribute__((packed)) {
  MACHeader mac;
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketDataRecieve;


typedef struct __attribute__((packed)) {
  uint8_t type;
  uint16_t packetIds[];
} LCMMPacketResponse;

typedef struct __attribute__((packed)) {
  uint8_t type;
  uint16_t id;
  uint8_t ackInterval; // amount of time after which ack is expected from the point the ack to the transmission has been recieved
  uint16_t packetIdStart; // number of packets being send
  uint16_t packetIdEnd; // number of packets to be sent
  unsigned char data[];
} LCMMPacketNegotiation;

typedef struct __attribute__((packed)) {
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketNegotiationResponse;

typedef struct __attribute__((packed)) {
  uint8_t type;
  uint16_t id;
  unsigned char data[];
} LCMMPacketData;

class LCMM {
public:
  static void RecievedPacket(int size);
  // Callback function type definition
  using DataReceivedCallback =
      function<void(LCMMPacketDataRecieve *data, uint32_t size)>;
  using AcknowledgmentCallback =
      function<void(uint16_t packetId, bool success)>;
      
 struct ACKWaitingSingle {
    AcknowledgmentCallback callback;
    LCMMPacketData *packet;
    int timeout;
    int timeLeft;
    uint16_t target;
    uint8_t size;
    uint16_t id;
    uint8_t attemptsLeft;
  };
  // Function to access the singleton instance
  static LCMM *getInstance();

  // Function to initialize the LCMM layer
  static void initialize(DataReceivedCallback dataRecieved,
                  AcknowledgmentCallback TransmissionComplete);

  // Function to handle incoming packets or events
  void handlePacket(/* Parameters as per your protocol */);

  void sendPacketLarge(uint16_t target, unsigned char *data, uint32_t size,
                       uint32_t timeout = 50000, uint8_t attempts = 8);

  uint16_t sendPacketSingle(bool needACK, uint16_t target, unsigned char *data,
                        uint8_t size, AcknowledgmentCallback callback,
                        uint32_t timeout = 10000, uint8_t attempts = 3);
  
  void loop();
  // Other member functions as needed

private:

  static bool sending;
  static void ReceivePacket(MACPacket *packet, uint16_t size, uint32_t correct);
  static ACKWaitingSingle ackWaitingSingle;
  static bool waitingForACKSingle;
  static uint16_t packetId;
  static LCMM *lcmm;
  static bool timeoutHandler();
  int lastTick;
  //static repeating_timer_t ackTimer;
  LCMM(DataReceivedCallback dataRecieved,
       AcknowledgmentCallback TransmissionComplete);

  // Private destructor
  ~LCMM();

  // Private copy constructor and assignment operator to prevent copying
  LCMM(const LCMM &) = delete;
  LCMM &operator=(const LCMM &) = delete;

  // Private member variables for LCMM layer
  DataReceivedCallback dataReceived;
  AcknowledgmentCallback transmissionComplete;

  void handleDataNoACK(LCMMPacketDataRecieve *data, uint16_t size);
  void handleDataACK(LCMMPacketDataRecieve *data, uint16_t size);
  void handleDataClusterACK(LCMMPacketDataRecieve *data, uint16_t size);
  void handleACK(LCMMPacketResponseRecieve *data, uint16_t size);
  void handlePacketNegotiation(LCMMPacketNegotiationRecieve *data, uint16_t size);

  // Private helper functions as needed
};

#endif // LCMM_LAYER_H
