#include "mathextension.h"
#include <DTPK.h>

DTPK *DTPK::dtpk = nullptr;

void DTPK::initialize(uint8_t KLimit)
{
  if (!dtpk)
    dtpk = new DTPK(KLimit);
}

DTPK *DTPK::getInstance()
{
  if (!dtpk)
  {
    return nullptr;
  }
  return dtpk;
}

DTPK::DTPK(uint8_t Klimit)
{
  this->Klimit = Klimit;
  this->packetCounter = 0;
  this->timeOfInit = millis();
  this->currentTime = timeOfInit;
  this->lastTick = 0;
  this->seed = MathExtension.murmur64(MAC::getInstance()->random()
                                          << 32 |
                                      MAC::getInstance()->random());

  randomSeed(this->seed);

  LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);

  MAC::getInstance()->setMode(RECEIVING, true);

  this->sendCrystPacket();
}

void DTPK::sendingDeamon()
{
  if (this->packetRequests.size() > 0)
  {
    for (int i = 0; i < this->packetRequests.size(); i++)
    {
      if (this->packetRequests[i].timeLeftToSend <= 0)
      {

        LCMM::getInstance()->sendPacketSingle(
            this->packetRequests[i].isAck,
            this->packetRequests[i].target,
            (unsigned char *)this->packetRequests[i].packet,
            this->packetRequests[i].size,
            DTPK::receiveAck,
            this->packetRequests[i].timeout);

        
        if (this->packetRequests[i].isAck)
        {
          DTPKPacketWaiting waiting;
          waiting.id = this->packetRequests[i].packet->id;
          waiting.timeout = this->packetRequests[i].timeout;
          waiting.timeLeft = waiting.timeout;
          waiting.gotAck = false;
          waiting.success = false;
          waiting.callback = this->packetRequests[i].callback;
          this->packetWaiting.push_back(waiting);
        }
        
        free(this->packetRequests[i].packet);
        this->packetRequests[i].packet = nullptr;

        this->packetRequests.erase(this->packetRequests.begin() + i);
      }
      else
      {
        this->packetRequests[i].timeLeftToSend -= currentTime - lastTick;
      }
    }
  }
}

void DTPK::timeoutDeamon()
{
  if (this->packetWaiting.size() > 0)
  {
    for (int i = 0; i < this->packetWaiting.size(); i++)
    {
      if(packetWaiting[i].gotAck == true){
        this->packetWaiting[i].callback(1, this->packetWaiting[i].timeout - this->packetWaiting[i].timeLeft);
        this->packetWaiting.erase(this->packetWaiting.begin() + i);
      }
      else
      if (this->packetWaiting[i].timeLeft <= 0)
      {
        this->packetWaiting[i].callback(0, 0);
        this->packetWaiting.erase(this->packetWaiting.begin() + i);
      }
      else
      {
        this->packetWaiting[i].timeLeft -= currentTime - lastTick;
      }
    }
  }
}

void DTPK::receivingDeamon()
{
  if (this->packetReceived.size() > 0)
  {
    pair<DTPKPacketGenericReceive*, size_t> packet = this->packetReceived.front();
    this->packetReceived.pop();

    switch (packet.first->type)
    {
    case CRYST:
      DTPKPacketCrystReceive *crystPacket = (DTPKPacketCrystReceive *)packet.first;
      size_t numberOfNeighbours = (packet.second - sizeof(DTPKPacketCrystReceive))/sizeof(NeighborRecord);
      
      bool shouldSendCrystPacket = this->crystDatabase.updateFromCrystPacket(crystPacket->lcmm.mac.sender, crystPacket->neighbors, numberOfNeighbours);
      
      if(shouldSendCrystPacket){
        this->sendCrystPacket();
      }

      break;
    case DATA_SINGLE:
      break;
    case ACK:
      break;
    case NACK_NOTFOUND:
      break;
    default:
      break;
    }
  }
}

void DTPK::loop()
{
  currentTime = millis();

  LCMM::getInstance()->loop();

  lastTick = currentTime;

}

void DTPK::addPacketToSendingQueue(DTPKPacketGeneric *packet,
                                   size_t size,
                                   uint16_t target,
                                   int16_t timeout,
                                   int16_t timeLeftToSend,
                                   bool isAck,
                                   PacketAckCallback callback)
{
  DTPKPacketRequest request;
  request.packet = packet;
  request.size = size;
  request.timeout = timeout;
  request.target = target;
  request.timeLeftToSend = timeLeftToSend;
  request.isAck = isAck;
  request.callback = callback;

  this->packetRequests.push_back(request);
}

DTPKPacketCryst *DTPK::prepareCrystPacket(size_t *size)
{
  vector<NeighborRecord> neighbors = this->crystDatabase.getListOfNeighbours();
  int numOfNeighbours = neighbors.size();
  *size = sizeof(DTPKPacketCryst) + sizeof(NeighborRecord) * numOfNeighbours;
  DTPKPacketCryst *packet = (DTPKPacketCryst *)malloc(*size);

  for (int i = 0; i < numOfNeighbours; i++)
  {
    packet->neighbors[i] = neighbors[i];
  }

  packet->type = CRYST;
  return packet;
}

void DTPK::sendCrystPacket()
{
  size_t size = 0;
  DTPKPacketCryst *packet = this->prepareCrystPacket(&size);
  this->addPacketToSendingQueue((DTPKPacketGeneric *)packet, size, BROADCAST, 5000, random(200, this->Klimit * 1000));
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint16_t size)
{
  /*if(packet->mac.sender == 5){// only for testing purposes
      free(packet);
      return;
  }*/

  DTPKPacketGenericReceive *dtpkPacket = (DTPKPacketGenericReceive *)packet->data;
  DTPK::getInstance()->packetReceived.push(make_pair(dtpkPacket, size));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
  for(int i = 0; i < DTPK::getInstance()->packetWaiting.size(); i++){
    if(DTPK::getInstance()->packetWaiting[i].id == id){
      DTPK::getInstance()->packetWaiting[i].gotAck = true;
      DTPK::getInstance()->packetWaiting[i].success = success;
      return;
    }
  }
}