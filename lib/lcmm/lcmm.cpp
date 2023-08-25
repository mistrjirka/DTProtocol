#include "include/lcmm.h"
LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;

void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc)
{
  if (/*crc != packet->crc32 ||*/ size <= 0)
  {
    // Serial.println("crc error %d %d \n", crc, packet->crc32);
    return;
  }

  uint8_t type = ((LCMMPacketUknownTypeRecieve *)packet)->type;
  Serial.println("RECIEVIED packet response type: " + String(type));

  switch (type)
  {
  case PACKET_TYPE_DATA_NOACK:
    LCMM::getInstance()->handleDataNoACK((LCMMPacketDataRecieve *)packet, size);
    break;

  case PACKET_TYPE_DATA_ACK:
    LCMM::getInstance()->handleDataACK((LCMMPacketDataRecieve *)packet, size);
    break;

  case PACKET_TYPE_ACK:
    LCMM::getInstance()->handleACK((LCMMPacketResponseRecieve *)packet, size);
    break;
    // Handle other packet types...
  default:
    // Handle unknown packet type
    break;
  }

  // Other member function implementations...

  if (type == PACKET_TYPE_ACK)
  {
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

void LCMM::handleDataNoACK(LCMMPacketDataRecieve *packet, uint16_t size)
{
  LCMM::getInstance()->dataReceived(packet, size);
}

void LCMM::handleDataACK(LCMMPacketDataRecieve *packet, uint16_t size)
{
  LCMMPacketResponse *response = (LCMMPacketResponse *)malloc(sizeof(LCMMPacketResponse) + 2);
  if (response == NULL)
  {
    Serial.println("Error allocating memory for response\n");
    return;
  }
  response->type = PACKET_TYPE_ACK;
  Serial.println("packet number" + String(packet->id));

  response->packetIds[0] = packet->id;

  MAC::getInstance()->sendData(packet->mac.sender, (unsigned char *)response,
                               sizeof(LCMMPacketResponse) + 2, 5000);
  LCMM::getInstance()->dataReceived(packet, size);
}

void LCMM::handleACK(LCMMPacketResponseRecieve *packet, uint16_t size)
{
  /*Serial.println("packet type ack received");
  Serial.println(" exxpected packet ID" + String(ackWaitingSingle.id) + " recieved Packet id: " + String(packet->packetIds[0]) + "  sender" + String(packet->mac.sender));
  Serial.println("starting BIN output");
  for (int i = 0; i < size; i++)
  {
    Serial.println(((uint8_t *)packet)[i]);
  }
  Serial.println("ending BIN output");
*/
  int numOfAcknowledgedPackets = (size - sizeof(LCMMPacketResponseRecieve))/sizeof(uint16_t);
  Serial.println("number of acknowledged packets: " + String(numOfAcknowledgedPackets) + "length of header wtf " + String(sizeof(LCMMPacketResponseRecieve))+" actual size "+ String(size));
  if (waitingForACKSingle && numOfAcknowledgedPackets == 1 &&
      ackWaitingSingle.id == packet->packetIds[0])
  {
    Serial.println("expected packet calling back");

    ackWaitingSingle.callback(ackWaitingSingle.id, true);

    if (ackWaitingSingle.packet != NULL)
    {
      free(ackWaitingSingle.packet);
    }
    ackWaitingSingle.packet = NULL;
    ackWaitingSingle.callback = NULL;
    waitingForACKSingle = false;
    sending = false;
  }
  else
  {
    Serial.println("unexpected ack");
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
