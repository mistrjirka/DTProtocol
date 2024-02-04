#include "include/lcmm.h"
LCMM *LCMM::lcmm = nullptr;
uint16_t LCMM::packetId = 1;
LCMM::ACKWaitingSingle LCMM::ackWaitingSingle;
bool LCMM::waitingForACKSingle = false;
bool LCMM::sending = false;
LCMMPacketDataReceive *LCMM::afterCallbackSent_packet = nullptr;
uint16_t LCMM::afterCallbackSent_size = 0;
void dummyFunction()
{
  //Serial.println("dummy function");
}
void LCMM::ReceivePacket(MACPacket *packet, uint16_t size, uint32_t crc)
{
  if (crc != packet->crc32 || size <= 0)
  {
    //Serial.println("crc error" + String(crc) + " recieved crc" + String(packet->crc32));
    return;
  }

  uint8_t type = ((LCMMPacketUknownTypeReceive *)packet)->type;
  //Serial.println("RECIEVIED packet response type: " + String(type));

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
    break;
    // Handle other packet types...
  default:
    // Handle unknown packet type
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


void LCMM::afterCallbackSent(){
  if(LCMM::afterCallbackSent_packet != nullptr){
      LCMM::getInstance()->dataReceived(LCMM::afterCallbackSent_packet, LCMM::afterCallbackSent_size);
  }
  MAC::getInstance()->setTransmitDone(dummyFunction);
}

void LCMM::handleDataACK(LCMMPacketDataReceive *packet, uint16_t size)
{

  LCMMPacketResponse *response = (LCMMPacketResponse *)malloc(sizeof(LCMMPacketResponse) + 2);
  if (response == NULL)
  {
    //Serial.println("Error allocating memory for response\n");
    return;
  }
  response->type = PACKET_TYPE_ACK;
  //Serial.println("packet number" + String(packet->id));

  response->packetIds[0] = packet->id;
  LCMM::afterCallbackSent_packet = packet;
  LCMM::afterCallbackSent_size = size;
  MAC::getInstance()->setTransmitDone(afterCallbackSent);

  MAC::getInstance()->sendData(packet->mac.sender, (unsigned char *)response,
                               sizeof(LCMMPacketResponse) + 2, 5000);
                              
}

void LCMM::handleACK(LCMMPacketResponseReceive *packet, uint16_t size)
{
  /*//Serial.println("packet type ack received");
  //Serial.println(" exxpected packet ID" + String(ackWaitingSingle.id) + " recieved Packet id: " + String(packet->packetIds[0]) + "  sender" + String(packet->mac.sender));
  //Serial.println("starting BIN output");
  for (int i = 0; i < size; i++)
  {
    //Serial.println(((uint8_t *)packet)[i]);
  }
  //Serial.println("ending BIN output");
*/

  int numOfAcknowledgedPackets = (size - sizeof(LCMMPacketResponseReceive)) / sizeof(uint16_t);
  //Serial.println("number of acknowledged packets: " + String(numOfAcknowledgedPackets) + "length of header wtf " + String(sizeof(LCMMPacketResponseReceive)) + " actual size " + String(size));
  if (waitingForACKSingle && numOfAcknowledgedPackets == 1 &&
      ackWaitingSingle.id == packet->packetIds[0])
  {
    //Serial.println("expected packet calling back");
    currentPing = millis() - packetSendStart;

    ackWaitingSingle.callback(ackWaitingSingle.id, true);

    this->clearSendingPacket();
  }
  else
  {
    //Serial.println("unexpected ack");
  }
}

LCMM *LCMM::getInstance()
{
  if (lcmm == nullptr)
  {
    // Throw an exception or handle the error case if initialize() has not been
    // called before getInstance()
    return nullptr;
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
        //Serial.println("\n\n\nTRANSMIT COMPLETELY FAILED \n\n\n");
        LCMM::ackWaitingSingle.callback(LCMM::ackWaitingSingle.id, false);
        LCMM::getInstance()->clearSendingPacket();

        return false;
      }
      else
      {
        //Serial.println("retransmitting");
        uint32_t timeBeforeSending = millis();

        MAC::getInstance()->sendData(LCMM::ackWaitingSingle.target,
                                     (unsigned char *)LCMM::ackWaitingSingle.packet,
                                     sizeof(LCMMPacketData) +
                                         LCMM::ackWaitingSingle.size,
                                     LCMM::ackWaitingSingle.timeout);
        uint32_t timeAfterSending = millis();

        LCMM::ackWaitingSingle.timeLeft = LCMM::ackWaitingSingle.timeout + timeBeforeSending - timeAfterSending;
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



uint16_t _id = 0;
LCMM::AcknowledgmentCallback _noAckcallback = nullptr;

void noAckCallback(){
  if(_noAckcallback != nullptr){
    _noAckcallback(_id, false);
    _noAckcallback = nullptr;
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
  if (LCMM::sending)
  {
    //Serial.println("already sending\n");
    return 0;
  }

  this->packetSendStart = millis();

  LCMMPacketData *packet =
      (LCMMPacketData *)malloc(sizeof(LCMMPacketData) + size);

  packet->id = LCMM::packetId++;
  packet->type = needACK ? 1 : 0;
  memcpy(packet->data, data, size);

  LCMM::sending = true;
  uint32_t timeBeforeSending = millis();
  if(!needACK){
    _noAckcallback = callback;
    _id = packet->id;
    MAC::getInstance()->setTransmitDone(noAckCallback);
  }else{
    MAC::getInstance()->setTransmitDone(dummyFunction);
  }

  MAC::getInstance()->sendData(target, (unsigned char *)packet,
                               sizeof(LCMMPacketData) + size, timeout);
  uint32_t timeAfterSending = millis();
  if (needACK)
  {
    waitingForACKSingle = true;
    ackWaitingSingle = prepareAckWaitingSingle(callback, timeout, packet,
                                               attempts, target, size, timeBeforeSending, timeAfterSending);
    lastTick = millis();
  }
  else
  {

    free(packet);
    LCMM::sending = false;
  }
  return packet->id;
}

LCMM::ACKWaitingSingle LCMM::prepareAckWaitingSingle(
    AcknowledgmentCallback callback, uint32_t timeout, LCMMPacketData *packet,
    uint8_t attemptsLeft, uint16_t target, uint8_t size, uint32_t timeBeforeSending, uint32_t timeAfterSending)
{
  //Serial.println("time on air: " + String(MathExtension.timeOnAir(size + MAC_OVERHEAD, 8, 9, 125, 7)));
  ACKWaitingSingle callbackStruct;
  callbackStruct.callback = callback;
  callbackStruct.timeout = timeout + MathExtension.timeOnAir(size + MAC_OVERHEAD, 8, 9, 125, 7);
  callbackStruct.id = packet->id;
  callbackStruct.packet = packet;
  callbackStruct.attemptsLeft = attemptsLeft;
  callbackStruct.timeLeft = timeout + timeBeforeSending - timeAfterSending + (int)MathExtension.timeOnAir(size + MAC_OVERHEAD, 8, 9, 125, 7);
  callbackStruct.target = target;
  callbackStruct.size = size;
  return callbackStruct;
}
