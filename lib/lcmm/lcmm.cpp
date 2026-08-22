#include "include/lcmm.h"

LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;
LCMMPacketDataReceive *LCMM::afterCallbackSent_packet = nullptr;
uint16_t LCMM::afterCallbackSent_size = 0;
uint16_t LCMM::noAckId = 0;
LCMM::AcknowledgmentCallback LCMM::noAckAcknowledgmentCallback = nullptr;

void dummyFunction()
{
}

void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc)
{
  if (crc != packet->crc32 || size <= 0)
  {
    free(packet);
    return;
  }

  uint8_t type = ((LCMMPacketUknownTypeReceive *)packet)->type;

  switch (type)
  {
  case PACKET_TYPE_DATA_NOACK:
    LCMM::getInstance()->handleDataNoACK((LCMMPacketDataReceive *)packet, size);
    break;

  case PACKET_TYPE_DATA_ACK:
    LCMM::getInstance()->handleDataACK((LCMMPacketDataReceive *)packet, size);
    break;

  case PACKET_TYPE_ACK:
    LCMM::getInstance()->handleACK((LCMMPacketResponseReceive *)packet, size);
    free(packet);
    break;

  default:
    free(packet);
    break;
  }
}

void LCMM::clearSendingPacket()
{
  if (LCMM::ackWaitingSingle.packet != NULL)
  {
    free(LCMM::ackWaitingSingle.packet);
  }
  LCMM::ackWaitingSingle.packet = NULL;
  LCMM::ackWaitingSingle.callback = NULL;
  LCMM::waitingForACKSingle = false;
  LCMM::sending = false;
}

void LCMM::handleDataNoACK(LCMMPacketDataReceive *packet, uint16_t size)
{
  LCMM::getInstance()->dataReceived(packet, size);
}

void LCMM::afterCallbackSent()
{
  LCMMPacketDataReceive *packet = LCMM::afterCallbackSent_packet;
  uint16_t size = LCMM::afterCallbackSent_size;
  LCMM::afterCallbackSent_packet = nullptr;
  LCMM::afterCallbackSent_size = 0;

  if (packet != nullptr)
  {
    LCMM::getInstance()->dataReceived(packet, size);
  }
  MAC::getInstance()->setTransmitDone(dummyFunction);
}

void LCMM::handleDataACK(LCMMPacketDataReceive *packet, uint16_t size)
{
  LCMMPacketResponse *response =
      (LCMMPacketResponse *)malloc(sizeof(LCMMPacketResponse) + sizeof(uint16_t));
  if (response == NULL)
  {
    free(packet);
    return;
  }

  response->type = PACKET_TYPE_ACK;
  response->packetIds[0] = packet->id;

  LCMM::afterCallbackSent_packet = packet;
  LCMM::afterCallbackSent_size = size;
  MAC::getInstance()->setTransmitDone(afterCallbackSent);

  uint8_t result = MAC::getInstance()->sendData(
      packet->mac.sender,
      (unsigned char *)response,
      sizeof(LCMMPacketResponse) + sizeof(uint16_t),
      5000);

  free(response);

  if (result != 0)
  {
    MAC::getInstance()->setTransmitDone(dummyFunction);
    LCMM::afterCallbackSent_packet = nullptr;
    LCMM::afterCallbackSent_size = 0;
    LCMM::getInstance()->dataReceived(packet, size);
  }
}

void LCMM::handleACK(LCMMPacketResponseReceive *packet, uint16_t size)
{
  int numOfAcknowledgedPackets =
      (size - sizeof(LCMMPacketResponseReceive)) / sizeof(uint16_t);

  if (waitingForACKSingle && numOfAcknowledgedPackets == 1 &&
      ackWaitingSingle.id == packet->packetIds[0])
  {
    currentPing = millis() - packetSendStart;

    if (ackWaitingSingle.callback)
      ackWaitingSingle.callback(ackWaitingSingle.id, true);

    this->clearSendingPacket();
  }
}

bool LCMM::isSending()
{
  return LCMM::sending || LCMM::waitingForACKSingle;
}

LCMM *LCMM::getInstance()
{
  return lcmm;
}

bool LCMM::timeoutHandler()
{
  if (LCMM::waitingForACKSingle)
  {
    uint32_t currTime = millis();
    uint32_t elapsed = currTime - LCMM::getInstance()->lastTick;
    LCMM::ackWaitingSingle.timeLeft -= (int)elapsed;

    if (LCMM::ackWaitingSingle.timeLeft <= 0)
    {
      if (--LCMM::ackWaitingSingle.attemptsLeft <= 0)
      {
        if (LCMM::ackWaitingSingle.callback)
          LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
        LCMM::getInstance()->clearSendingPacket();
        return false;
      }
      else
      {
        uint32_t timeBeforeSending = millis();
        uint8_t result = MAC::getInstance()->sendData(
            LCMM::ackWaitingSingle.target,
            (unsigned char *)LCMM::ackWaitingSingle.packet,
            sizeof(LCMMPacketData) + LCMM::ackWaitingSingle.size,
            LCMM::ackWaitingSingle.timeout);
        uint32_t timeAfterSending = millis();

        if (result == 0)
        {
          LCMM::ackWaitingSingle.timeLeft =
              LCMM::ackWaitingSingle.timeout +
              (int)(timeBeforeSending - timeAfterSending);
        }
        else
        {
          LCMM::ackWaitingSingle.attemptsLeft++;
          LCMM::ackWaitingSingle.timeLeft = 1;
        }
      }
    }
    LCMM::getInstance()->lastTick = currTime;
  }
  return true;
}

void LCMM::initialize(DataReceivedCallback dataReceived,
                      AcknowledgmentCallback transmissionComplete)
{
  if (lcmm == nullptr)
  {
    lcmm = new LCMM(dataReceived, transmissionComplete);
  }
  MAC::getInstance()->setRXCallback(LCMM::ReceivePacket);
}

LCMM::LCMM(DataReceivedCallback dataReceived,
           AcknowledgmentCallback transmissionComplete)
{
  this->dataReceived = dataReceived;
  this->transmissionComplete = transmissionComplete;
  this->lastTick = millis();
  this->packetSendStart = millis();
}

LCMM::~LCMM()
{
}

void LCMM::handlePacket()
{
}

void LCMM::loop()
{
  MAC::getInstance()->loop();
  this->timeoutHandler();
}

void LCMM::noAckTransmitDone()
{
  LCMM::sending = false;
  if (LCMM::noAckAcknowledgmentCallback)
  {
    LCMM::noAckAcknowledgmentCallback(LCMM::noAckId, true);
    LCMM::noAckAcknowledgmentCallback = nullptr;
  }
  MAC::getInstance()->setTransmitDone(dummyFunction);
}

void LCMM::sendPacketLarge(uint16_t target, unsigned char *data, uint32_t size,
                           uint32_t timeout, uint8_t attempts) {}

uint16_t LCMM::sendPacketSingle(bool needACK, uint16_t target,
                                unsigned char *data, uint8_t size,
                                AcknowledgmentCallback callback,
                                uint32_t timeout, uint8_t attempts)
{
  if (LCMM::sending || LCMM::waitingForACKSingle)
  {
    return 0;
  }

  this->packetSendStart = millis();

  LCMMPacketData *packet =
      (LCMMPacketData *)malloc(sizeof(LCMMPacketData) + size);
  if (!packet)
  {
    if (callback) callback(0, false);
    return 0;
  }

  packet->id = LCMM::packetId++;
  packet->type = needACK ? PACKET_TYPE_DATA_ACK : PACKET_TYPE_DATA_NOACK;
  memcpy(packet->data, data, size);
  const uint16_t outgoingId = packet->id;

  LCMM::sending = true;
  uint32_t timeBeforeSending = millis();

  if (!needACK)
  {
    LCMM::noAckAcknowledgmentCallback = callback;
    LCMM::noAckId = outgoingId;
    MAC::getInstance()->setTransmitDone(LCMM::noAckTransmitDone);
  }
  else
  {
    MAC::getInstance()->setTransmitDone(dummyFunction);
  }

  uint8_t result = MAC::getInstance()->sendData(
      target,
      (unsigned char *)packet,
      sizeof(LCMMPacketData) + size,
      timeout);
  uint32_t timeAfterSending = millis();

  if (result != 0)
  {
    if (!needACK)
    {
      LCMM::noAckAcknowledgmentCallback = nullptr;
      MAC::getInstance()->setTransmitDone(dummyFunction);
    }
    LCMM::sending = false;
    free(packet);
    if (callback) callback(outgoingId, false);
    return 0;
  }

  if (needACK)
  {
    waitingForACKSingle = true;
    ackWaitingSingle = prepareAckWaitingSingle(
        callback, timeout, packet, attempts, target, size,
        timeBeforeSending, timeAfterSending);
    lastTick = millis();
  }
  else
  {
    free(packet);
  }

  return outgoingId;
}

LCMM::ACKWaitingSingle LCMM::prepareAckWaitingSingle(
    AcknowledgmentCallback callback, uint32_t timeout, LCMMPacketData *packet,
    uint8_t attemptsLeft, uint16_t target, uint8_t size,
    uint32_t timeBeforeSending, uint32_t timeAfterSending)
{
  ACKWaitingSingle callbackStruct;
  callbackStruct.callback = callback;
  callbackStruct.timeout =
      timeout + MathExtension.timeOnAir(size + MAC_OVERHEAD, 8, 9, 125, 7);
  callbackStruct.id = packet->id;
  callbackStruct.packet = packet;
  callbackStruct.attemptsLeft = attemptsLeft;
  callbackStruct.timeLeft =
      timeout + timeBeforeSending - timeAfterSending +
      (int)MathExtension.timeOnAir(size + MAC_OVERHEAD, 8, 9, 125, 7);
  callbackStruct.target = target;
  callbackStruct.size = size;
  return callbackStruct;
}
