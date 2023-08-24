#include "include/lcmm.h"
LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;

// struct repeating_timer LCMM::ackTimer;
void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc)
{
  if (/*crc != packet->crc32 ||*/ size <= 0)
  {
    // Serial.println("crc error %d %d \n", crc, packet->crc32);
    return;
  }

  uint8_t type = ((LCMMPacketUknownTypeRecieve *)packet)->type;
  Serial.println("RECIEVIED packet response type: " + String(type));

  if (type == PACKET_TYPE_DATA_NOACK)
  {
    LCMMPacketDataRecieve *data = (LCMMPacketDataRecieve *)packet;
    packet = NULL;
    LCMM::getInstance()->dataReceived(data,
                                      size - sizeof(LCMMPacketDataRecieve));
  }
  else if (type == PACKET_TYPE_DATA_ACK)
  {
    LCMMPacketDataRecieve *data = (LCMMPacketDataRecieve *)packet;
    packet = NULL;
    LCMM::getInstance()->dataReceived(data, size);
    LCMMPacketResponse *response = (LCMMPacketResponse *)malloc(sizeof(LCMMPacketResponse));
    if (response == NULL)
    {
      Serial.println("Error allocating memory for response\n");
      return;
    }
    response->type = PACKET_TYPE_ACK;
    Serial.println("packet number"+String(data->id));
    
    response->packetIds[0] = data->id;
    
    MAC::getInstance()->sendData(data->mac.sender, (unsigned char *)response,
                                 sizeof(LCMMPacketResponse), 5000);
  }
  else if (type == PACKET_TYPE_ACK)
  {
    Serial.println("packet type ack received");
    LCMMPacketResponseRecieve *response = (LCMMPacketResponseRecieve *)packet;
    Serial.println(" exxpected packet ID" + String(ackWaitingSingle.id) + " recieved Packet id: "+ String(response->packetIds[0])
    + "  sender" + String(response->mac.sender));
    Serial.println("starting BIN output");
    for(int i = 0; i < size; i++){
       char buffer[40];
      sprintf(buffer,BYTE_TO_BINARY_PATTERN, BYTE_TO_BINARY(((byte *)packet)[i]));
      Serial.println(buffer);
    }
        Serial.println("ending BIN output");

    if (waitingForACKSingle &&
        ackWaitingSingle.id == response->packetIds[0])
    {
      Serial.println("expected packet calling back");

      ackWaitingSingle.callback(ackWaitingSingle.id, true);
      // cancel_repeating_timer(&LCMM::ackTimer);
      if (ackWaitingSingle.packet != NULL)
      {
        free(ackWaitingSingle.packet);
      }
      ackWaitingSingle.packet = NULL;
      ackWaitingSingle.callback = NULL;
      waitingForACKSingle = false;
      sending = false;
    }else{
      Serial.println("unexpected ack");
    }
  }
  else if (type == PACKET_TYPE_DATA_CLUSTER_ACK)
  {
    // Handle PACKET_TYPE_DATA_CLUSTER_ACK
  }
  else if (type == PACKET_TYPE_PACKET_NEGOTIATION ||
           type == PACKET_TYPE_PACKET_NEGOTIATION_REFUSED ||
           type == PACKET_TYPE_PACKET_NEGOTIATION_ACCEPTED)
  {
    // Handle PACKET_TYPE_PACKET_NEGOTIATION,
    // PACKET_TYPE_PACKET_NEGOTIATION_REFUSED,
    // PACKET_TYPE_PACKET_NEGOTIATION_ACCEPTED
  }
}

LCMM *LCMM::getInstance()
{
  if (lcmm == nullptr)
  {
    // Throw an exception or handle the error case if initialize() has not been
    // called before getInstance()
  }
  return lcmm;
}

bool LCMM::timeoutHandler()
{

  if (LCMM::waitingForACKSingle)
  {
    int currTime = millis();
    LCMM::ackWaitingSingle.timeLeft -= currTime - LCMM::getInstance()->lastTick;
    if (LCMM::ackWaitingSingle.timeLeft <= 0)
    {
      if (--LCMM::ackWaitingSingle.attemptsLeft <= 0)
      {
        Serial.println("\n\n\nTRANSMIT COMPLETELY FAILED \n\n\n");
        LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
        if (LCMM::ackWaitingSingle.packet != NULL)
        {
          free(LCMM::ackWaitingSingle.packet);
        }
        LCMM::ackWaitingSingle.packet = NULL;
        LCMM::ackWaitingSingle.callback = NULL;
        LCMM::waitingForACKSingle = false;
        LCMM::sending = false;

        return false;
      }
      else
      {
        LCMM::ackWaitingSingle.timeLeft = LCMM::ackWaitingSingle.timeout;
        Serial.println("retransmitting");
        MAC::getInstance()->sendData(LCMM::ackWaitingSingle.target,
                                     (unsigned char *)LCMM::ackWaitingSingle.packet,
                                     sizeof(LCMMPacketData) +
                                         LCMM::ackWaitingSingle.size,
                                     LCMM::ackWaitingSingle.timeout);
      }
    }
    LCMM::getInstance()->lastTick = currTime;
  }
  return true;
}

void LCMM::initialize(DataReceivedCallback dataRecieved,
                      AcknowledgmentCallback transmissionComplete)
{
  if (lcmm == nullptr)
  {
    lcmm = new LCMM(dataRecieved, transmissionComplete);
  }
  MAC::getInstance()->setRXCallback(LCMM::ReceivePacket);
}

LCMM::LCMM(DataReceivedCallback dataRecieved,
           AcknowledgmentCallback transmissionComplete)
{
  this->dataReceived = dataRecieved;
  this->transmissionComplete = transmissionComplete;
}

LCMM::~LCMM()
{
  // Destructor implementation if needed
}

void LCMM::handlePacket(/* Parameters as per your protocol */)
{
  // Handle incoming packets or events logic
  // You can invoke the stored callback function or pass the provided callback
  // function as needed For example, invoking the stored callback function:
}

void LCMM::loop()
{
  MAC::getInstance()->loop();
  this->timeoutHandler();
}

void LCMM::sendPacketLarge(uint16_t target, unsigned char *data, uint32_t size,
                           uint32_t timeout, uint8_t attempts) {}

uint16_t LCMM::sendPacketSingle(bool needACK, uint16_t target,
                                unsigned char *data, uint8_t size,
                                AcknowledgmentCallback callback,
                                uint32_t timeout, uint8_t attempts)
{
  if (LCMM::sending)
  {
    Serial.println("already sending\n");
    return 0;
  }
  LCMMPacketData *packet =
      (LCMMPacketData *)malloc(sizeof(LCMMPacketData) + size);
  packet->id = LCMM::packetId++;
  packet->type = needACK ? 1 : 0;
  memcpy(packet->data, data, size);
  LCMM::sending = true;
  MAC::getInstance()->sendData(target, (unsigned char *)packet,
                               sizeof(LCMMPacketData) + size, timeout);
  if (needACK)
  {
    ACKWaitingSingle callbackStruct;
    callbackStruct.callback = callback;
    callbackStruct.timeout = timeout;
    callbackStruct.id = packet->id;
    callbackStruct.packet = packet;
    callbackStruct.attemptsLeft = attempts;
    callbackStruct.timeLeft = timeout;
    callbackStruct.target = target;
    callbackStruct.size = size;
    waitingForACKSingle = true;
    // add_repeating_timer_ms(timeout, timeoutHandler, NULL, &LCMM::ackTimer);
    ackWaitingSingle = callbackStruct;
    lastTick = millis();
  }
  else
  {
    free(packet);
    LCMM::sending = false;
  }
  return packet->id;
}
