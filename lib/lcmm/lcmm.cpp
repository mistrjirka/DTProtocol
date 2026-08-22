#include "include/lcmm.h"

#include <algorithm>
#include <cmath>
#include <limits>

LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;
LCMMPacketDataReceive *LCMM::afterCallbackSent_packet = nullptr;
uint16_t LCMM::afterCallbackSent_size = 0;
uint16_t LCMM::noAckId = 0;
LCMM::AcknowledgmentCallback LCMM::noAckAcknowledgmentCallback = nullptr;

namespace
{
bool transientMacFailure(uint8_t result)
{
  return result == MAC_SEND_BUSY ||
         result == MAC_SEND_CHANNEL_BUSY_TIMEOUT ||
         result == MAC_SEND_DUTY_CYCLE;
}

int clampTimeoutToInt(uint64_t value)
{
  const uint64_t limit = static_cast<uint64_t>(std::numeric_limits<int>::max());
  return static_cast<int>(std::min(value, limit));
}
} // namespace

void dummyFunction()
{
}

void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc)
{
  if (!packet)
    return;

  if (crc != packet->crc32 || size < sizeof(MACHeader) + sizeof(uint8_t))
  {
    free(packet);
    return;
  }

  const uint8_t type = ((LCMMPacketUknownTypeReceive *)packet)->type;

  switch (type)
  {
  case PACKET_TYPE_DATA_NOACK:
    if (size >= sizeof(LCMMPacketDataReceive))
      LCMM::getInstance()->handleDataNoACK((LCMMPacketDataReceive *)packet, size);
    else
      free(packet);
    break;

  case PACKET_TYPE_DATA_ACK:
    if (size >= sizeof(LCMMPacketDataReceive))
      LCMM::getInstance()->handleDataACK((LCMMPacketDataReceive *)packet, size);
    else
      free(packet);
    break;

  case PACKET_TYPE_ACK:
    if (size >= sizeof(LCMMPacketResponseReceive) + sizeof(uint16_t))
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
    free(LCMM::ackWaitingSingle.packet);

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
  const uint16_t size = LCMM::afterCallbackSent_size;
  LCMM::afterCallbackSent_packet = nullptr;
  LCMM::afterCallbackSent_size = 0;

  if (packet != nullptr)
    LCMM::getInstance()->dataReceived(packet, size);

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

  const uint8_t result = MAC::getInstance()->sendData(
      packet->mac.sender,
      (unsigned char *)response,
      sizeof(LCMMPacketResponse) + sizeof(uint16_t),
      5000);

  free(response);

  if (result != MAC_SEND_OK)
  {
    MAC::getInstance()->setTransmitDone(dummyFunction);
    LCMM::afterCallbackSent_packet = nullptr;
    LCMM::afterCallbackSent_size = 0;
    // Upper DTPK replay suppression makes a later sender retry idempotent.
    LCMM::getInstance()->dataReceived(packet, size);
  }
}

void LCMM::handleACK(LCMMPacketResponseReceive *packet, uint16_t size)
{
  if (size < sizeof(LCMMPacketResponseReceive) + sizeof(uint16_t))
    return;

  const size_t payloadBytes = size - sizeof(LCMMPacketResponseReceive);
  const size_t numOfAcknowledgedPackets = payloadBytes / sizeof(uint16_t);

  if (waitingForACKSingle &&
      numOfAcknowledgedPackets == 1 &&
      ackWaitingSingle.id == packet->packetIds[0] &&
      ackWaitingSingle.target == packet->mac.sender)
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
    const uint32_t currTime = millis();
    const uint32_t elapsed = currTime - LCMM::getInstance()->lastTick;
    LCMM::ackWaitingSingle.timeLeft -= static_cast<int>(elapsed);

    if (LCMM::ackWaitingSingle.timeLeft <= 0)
    {
      if (--LCMM::ackWaitingSingle.attemptsLeft <= 0)
      {
        if (LCMM::ackWaitingSingle.callback)
          LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
        LCMM::getInstance()->clearSendingPacket();
        return false;
      }

      const uint32_t timeBeforeSending = millis();
      const uint8_t result = MAC::getInstance()->sendData(
          LCMM::ackWaitingSingle.target,
          (unsigned char *)LCMM::ackWaitingSingle.packet,
          static_cast<uint8_t>(sizeof(LCMMPacketData) + LCMM::ackWaitingSingle.size),
          static_cast<uint32_t>(LCMM::ackWaitingSingle.timeout));
      const uint32_t timeAfterSending = millis();
      LCMM::getInstance()->lastSendResult = result;

      if (result == MAC_SEND_OK)
      {
        LCMM::ackWaitingSingle.timeLeft =
            LCMM::ackWaitingSingle.timeout +
            static_cast<int>(timeBeforeSending - timeAfterSending);
      }
      else if (transientMacFailure(result))
      {
        // No RF attempt happened. Restore the reliability attempt and wake when
        // the MAC policy permits a real retry instead of consuming retry budget.
        LCMM::ackWaitingSingle.attemptsLeft++;
        const uint32_t macWait = MAC::getInstance()->getTransmitWaitMs();
        LCMM::ackWaitingSingle.timeLeft =
            macWait > 0 ? clampTimeoutToInt(macWait) : 1;
      }
      else
      {
        // Allocation/config/radio errors are not carrier contention. Retrying
        // forever hides a real hardware fault and can permanently wedge LCMM.
        if (LCMM::ackWaitingSingle.callback)
          LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
        LCMM::getInstance()->clearSendingPacket();
        return false;
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
    lcmm = new LCMM(dataReceived, transmissionComplete);

  MAC::getInstance()->setRXCallback(LCMM::ReceivePacket);
}

LCMM::LCMM(DataReceivedCallback dataReceived,
           AcknowledgmentCallback transmissionComplete)
{
  this->dataReceived = dataReceived;
  this->transmissionComplete = transmissionComplete;
  this->lastTick = millis();
  this->packetSendStart = millis();
  this->lastSendResult = MAC_SEND_OK;
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
    this->lastSendResult = MAC_SEND_BUSY;
    return 0;
  }

  // Avoid packet allocation/churn while the MAC already knows it cannot send.
  if (MAC::getInstance()->getTransmitWaitMs() > 0)
  {
    this->lastSendResult = MAC_SEND_CHANNEL_BUSY_TIMEOUT;
    return 0;
  }

  const size_t macPayloadBytes = sizeof(LCMMPacketData) + static_cast<size_t>(size);
  if (macPayloadBytes > DATASIZE_MAC || macPayloadBytes > UINT8_MAX)
  {
    this->lastSendResult = MAC_SEND_TOO_LARGE;
    if (callback)
      callback(0, false);
    return 0;
  }

  this->packetSendStart = millis();

  LCMMPacketData *packet =
      (LCMMPacketData *)malloc(sizeof(LCMMPacketData) + size);
  if (!packet)
  {
    this->lastSendResult = MAC_SEND_ALLOC_FAILED;
    if (callback)
      callback(0, false);
    return 0;
  }

  packet->id = LCMM::packetId++;
  packet->type = needACK ? PACKET_TYPE_DATA_ACK : PACKET_TYPE_DATA_NOACK;
  memcpy(packet->data, data, size);
  const uint16_t outgoingId = packet->id;

  LCMM::sending = true;
  const uint32_t timeBeforeSending = millis();

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

  const uint8_t result = MAC::getInstance()->sendData(
      target,
      (unsigned char *)packet,
      static_cast<uint8_t>(macPayloadBytes),
      timeout);
  const uint32_t timeAfterSending = millis();
  this->lastSendResult = result;

  if (result != MAC_SEND_OK)
  {
    if (!needACK)
    {
      LCMM::noAckAcknowledgmentCallback = nullptr;
      MAC::getInstance()->setTransmitDone(dummyFunction);
    }
    LCMM::sending = false;
    free(packet);

    // Policy denial is not a failed RF attempt. The owner still has the DTPK
    // bytes and can retry after getTransmitWaitMs(). Fatal errors are surfaced.
    if (!transientMacFailure(result) && callback)
      callback(outgoingId, false);
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

  // MAC actually transmits MAC header + LCMM header + DTPK payload. The old
  // calculation omitted sizeof(LCMMPacketData), underestimating TX completion
  // by up to a complete LoRa symbol group.
  const uint16_t fullFrameBytes = static_cast<uint16_t>(
      MAC_OVERHEAD + sizeof(LCMMPacketData) + static_cast<size_t>(size));
  const float airtimeFloat = MathExtension.timeOnAir(
      fullFrameBytes, 8, 9, 125.0f, 7);
  const uint32_t airtimeMs =
      airtimeFloat > 0.0f && std::isfinite(airtimeFloat)
          ? static_cast<uint32_t>(std::ceil(airtimeFloat))
          : 0u;

  const uint64_t timeoutWithAirtime =
      static_cast<uint64_t>(timeout) + airtimeMs;
  callbackStruct.timeout = clampTimeoutToInt(timeoutWithAirtime);
  callbackStruct.id = packet->id;
  callbackStruct.packet = packet;
  callbackStruct.attemptsLeft = attemptsLeft;

  const int64_t elapsedInsideSend =
      static_cast<int64_t>(timeAfterSending) - static_cast<int64_t>(timeBeforeSending);
  const int64_t initialTime =
      static_cast<int64_t>(timeout) + static_cast<int64_t>(airtimeMs) - elapsedInsideSend;
  callbackStruct.timeLeft =
      initialTime <= 1
          ? 1
          : clampTimeoutToInt(static_cast<uint64_t>(initialTime));
  callbackStruct.target = target;
  callbackStruct.size = size;
  return callbackStruct;
}
