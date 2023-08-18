#include "include/lcmm.h"
LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;

//struct repeating_timer LCMM::ackTimer;
void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc) {
  if (/*crc != packet->crc32 ||*/ size <= 0) {
    printf("crc error %d %d \n", crc, packet->crc32);
    return;
  }
  uint8_t type = ((LCMMPacketUknownTypeRecieve *)packet)->type;
  if (type == PACKET_TYPE_DATA_NOACK) {
    LCMMPacketDataRecieve *data = (LCMMPacketDataRecieve *)packet;
    packet = NULL;
    LCMM::getInstance()->dataReceived(data,
                                      size - sizeof(LCMMPacketDataRecieve));
  } else if (type == PACKET_TYPE_DATA_ACK) {
    LCMMPacketDataRecieve *data = (LCMMPacketDataRecieve *)packet;
    packet = NULL;
    LCMM::getInstance()->dataReceived(data, size);
    LCMMPacketResponseRecieve *response = (LCMMPacketResponseRecieve *)malloc(
        sizeof(LCMMPacketResponseRecieve) + 1);
    if (response == NULL) {
      printf("Error allocating memory for response\n");
      return;
    }
    response->type = PACKET_TYPE_ACK;
    response->packetIds[0] = data->id;
    MAC::getInstance()->sendData(data->mac.sender, (unsigned char *)response,
                                 sizeof(LCMMPacketResponseRecieve) + 2, false,
                                 5000);

  } else if (type == PACKET_TYPE_ACK) {

    LCMMPacketResponseRecieve *response = (LCMMPacketResponseRecieve *)packet;
    if (response->type == PACKET_TYPE_ACK) {
      if (waitingForACKSingle &&
          size - sizeof(LCMMPacketResponseRecieve) == 2 &&
          ackWaitingSingle.id == response->packetIds[0]) {
        ackWaitingSingle.callback(ackWaitingSingle.id, true);
        //cancel_repeating_timer(&LCMM::ackTimer);
        if (ackWaitingSingle.packet != NULL) {
          free(ackWaitingSingle.packet);
        }
        ackWaitingSingle.packet = NULL;
        ackWaitingSingle.callback = NULL;
        waitingForACKSingle = false;
        sending = false;
      }
    }

  } else if (type == PACKET_TYPE_DATA_CLUSTER_ACK) {
    // Handle PACKET_TYPE_DATA_CLUSTER_ACK
  } else if (type == PACKET_TYPE_PACKET_NEGOTIATION ||
             type == PACKET_TYPE_PACKET_NEGOTIATION_REFUSED ||
             type == PACKET_TYPE_PACKET_NEGOTIATION_ACCEPTED) {
    // Handle PACKET_TYPE_PACKET_NEGOTIATION,
    // PACKET_TYPE_PACKET_NEGOTIATION_REFUSED,
    // PACKET_TYPE_PACKET_NEGOTIATION_ACCEPTED
  }
}

LCMM *LCMM::getInstance() {
  if (lcmm == nullptr) {
    // Throw an exception or handle the error case if initialize() has not been
    // called before getInstance()
  }
  return lcmm;
}

bool LCMM::timeoutHandler(struct repeating_timer *t) {
  if (--LCMM::ackWaitingSingle.attemptsLeft <= 0 ||
      !LCMM::waitingForACKSingle) {
    printf("transmission failed\n");
    if (LCMM::waitingForACKSingle) {
      LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
    }
    //cancel_repeating_timer(&LCMM::ackTimer);
    if (LCMM::ackWaitingSingle.packet != NULL) {
      free(LCMM::ackWaitingSingle.packet);
    }
    LCMM::ackWaitingSingle.packet = NULL;
    LCMM::ackWaitingSingle.callback = NULL;
    LCMM::waitingForACKSingle = false;
    LCMM::sending = false;

    return false;
  } else {
    printf("retransmitting");
    MAC::getInstance()->sendData(LCMM::ackWaitingSingle.target,
                                 (unsigned char *)LCMM::ackWaitingSingle.packet,
                                 sizeof(LCMMPacketData) +
                                     LCMM::ackWaitingSingle.size,
                                 true, LCMM::ackWaitingSingle.timeout);
  }
  printf("timer out");
  return true;
}

void LCMM::initialize(DataReceivedCallback dataRecieved,
                      AcknowledgmentCallback transmissionComplete) {
  if (lcmm == nullptr) {
    lcmm = new LCMM(dataRecieved, transmissionComplete);
  }
  MAC::getInstance()->setRXCallback(LCMM::ReceivePacket);
}
LCMM::LCMM(DataReceivedCallback dataRecieved,
           AcknowledgmentCallback transmissionComplete) {
  this->dataReceived = dataRecieved;
  this->transmissionComplete = transmissionComplete;
}

LCMM::~LCMM() {
  // Destructor implementation if needed
}

void LCMM::handlePacket(/* Parameters as per your protocol */) {
  // Handle incoming packets or events logic
  // You can invoke the stored callback function or pass the provided callback
  // function as needed For example, invoking the stored callback function:
}

void LCMM::sendPacketLarge(uint16_t target, unsigned char *data, uint32_t size,
                           uint32_t timeout, uint8_t attempts) {}

uint16_t LCMM::sendPacketSingle(bool needACK, uint16_t target,
                                unsigned char *data, uint8_t size,
                                AcknowledgmentCallback callback,
                                uint32_t timeout, uint8_t attempts) {
  if (LCMM::sending) {
    printf("already sending\n");
    return 0;
  }
  LCMMPacketData *packet =
      (LCMMPacketData *)malloc(sizeof(LCMMPacketData) + size);
  packet->id = LCMM::packetId++;
  packet->type = needACK ? 1 : 0;
  memcpy(packet->data, data, size);
  LCMM::sending = true;
  MAC::getInstance()->sendData(target, (unsigned char *)packet,
                               sizeof(LCMMPacketData) + size, false, timeout);
  if (needACK) {
    ACKWaitingSingle callbackStruct;
    callbackStruct.callback = callback;
    callbackStruct.timeout = timeout;
    callbackStruct.id = packet->id;
    callbackStruct.packet = packet;
    callbackStruct.attemptsLeft = attempts;
    callbackStruct.target = target;
    callbackStruct.size = size;
    waitingForACKSingle = true;
    //add_repeating_timer_ms(timeout, timeoutHandler, NULL, &LCMM::ackTimer);
    ackWaitingSingle = callbackStruct;
  } else {
    free(packet);
    LCMM::sending = false;
  }
  return packet->id;
}
