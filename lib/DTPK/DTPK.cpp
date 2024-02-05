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

DTPK::DTPK(uint8_t Klimit) : crystDatabase(MAC::getInstance()->getId())
{
  Serial.println(F("Initializing library DTPK..."));

  this->Klimit = Klimit;
  this->packetCounter = 0;
  this->timeOfInit = millis();
  this->currentTime = timeOfInit;
  this->lastTick = 0;
  this->crystTimeout.remaining = 0;
  this->seed = MathExtension.murmur64((uint64_t) MAC::getInstance()->random()
                                          << 32 |
                                      MAC::getInstance()->random());
  printf("Seed: %d\n", this->seed);

  randomSeed(this->seed);

  LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);

  MAC::getInstance()->setMode(RECEIVING, true);

  this->sendCrystPacket();
}

void DTPK::setPacketReceivedCallback(DTPK::PacketReceivedCallback callback)
{
 this->recieveCallback = callback;
}

void DTPK::sendingDeamon()
{
  if (this->packetRequests.size() > 0)
  {
    for (unsigned int i = 0; i < this->packetRequests.size(); i++)
    {
      if (this->packetRequests[i].timeLeftToSend <= 0)
      {
        Serial.println(F("sending packet"));

        printf("sending packet\n");
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
        
        //free(this->packetRequests[i].packet);
        this->packetRequests[i].packet = nullptr;

        this->packetRequests.erase(this->packetRequests.begin() + i);
      }
      else
      {
        Serial.println("time left to send: " + String(this->packetRequests[i].timeLeftToSend));
        printf("time left to send: %d\n", this->packetRequests[i].timeLeftToSend);
        this->packetRequests[i].timeLeftToSend -= currentTime - lastTick;
      }
    }
  }
}

void DTPK::timeoutDeamon()
{
  if (this->packetWaiting.size() > 0)
  {
    for (unsigned int i = 0; i < this->packetWaiting.size(); i++)
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

void DTPK::parseCrystPacket(pair<DTPKPacketUnknownReceive*, size_t> packet)
{
      printf("parsing packet\n");
      DTPKPacketCrystReceive *crystPacket = (DTPKPacketCrystReceive *)packet.first;
      size_t numberOfNeighbours = (packet.second - sizeof(DTPKPacketCrystReceive))/sizeof(NeighborRecord);
      if(!this->crystDatabase.isInCrystalizationSession()){
        this->crystDatabase.startCrystalizationSession();
      }
      this->crystTimeout.remaining = this->Klimit*1000;

      bool shouldSendCrystPacket = this->crystDatabase.updateFromCrystPacket(crystPacket->lcmm.mac.sender, crystPacket->neighbors, numberOfNeighbours);
      
      if(shouldSendCrystPacket){
        this->sendCrystPacket();
      }
}

void DTPK::parseSingleDataPacket(pair<DTPKPacketUnknownReceive*, size_t> packet)
{
  DTPKPacketGenericReceive *dataPacket = (DTPKPacketGenericReceive *)packet.first;
  
  if (dataPacket->finalTarget == MAC::getInstance()->getId())
  {
    this->recieveCallback(dataPacket, packet.second);
    this->sendAckPacket(dataPacket->originalSender, dataPacket->id);

  }else if(this->crystDatabase.getRouting(dataPacket->finalTarget) != nullptr)
  {
    
    this->addPacketToSendingQueue((DTPKPacketUnknown *)dataPacket, packet.second, dataPacket->finalTarget, 5000, 0);
  }else
  {
    this->sendNackPacket(dataPacket->originalSender, dataPacket->id);
  }
}


void DTPK::receivingDeamon()
{
  if (this->packetReceived.size() > 0)
  {
    pair<DTPKPacketUnknownReceive*, size_t> packet = this->packetReceived.front();
    this->packetReceived.pop();
    DTPKPacketUnknownReceive *dtpkPacket = packet.first;

    printf("parsing packet sender: %hu type: %hhu id: %hu size: %u target: %hu lcmm type%hhu\n", 
    (unsigned int)dtpkPacket->lcmm.mac.sender, dtpkPacket->type, dtpkPacket->id, packet.second, dtpkPacket->lcmm.mac.target, dtpkPacket->lcmm.type);

    switch (packet.first->type)
    {
    case CRYST:
      printf("cryst packet\n ");
      this->parseCrystPacket(packet);
      break;
    case DATA_SINGLE:
      this->parseSingleDataPacket(packet);
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

vector<NeighborRecord> DTPK::getNeighbours()
{
  return this->crystDatabase.getListOfNeighbours();
}


void DTPK::crystTimeoutDeamon()
{
  if(this->crystDatabase.isInCrystalizationSession())
  {
    if(this->crystTimeout.remaining <= 0)
    {
      bool updated = this->crystDatabase.endCrystalizationSession();
      if(updated)
      {
        this->sendCrystPacket();
      }
    }
    else
    {
      this->crystTimeout.remaining -= currentTime - lastTick;
    }
  }
}

void DTPK::loop()
{
  currentTime = millis();

  this->receivingDeamon();
  this->sendingDeamon();
  this->timeoutDeamon();
  this->crystTimeoutDeamon();

  LCMM::getInstance()->loop();

  lastTick = currentTime;

}

void DTPK::addPacketToSendingQueue(DTPKPacketUnknown *packet,
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

  printf("adding packet to sending queue packet \n");
  this->packetRequests.push_back(request);
}

DTPKPacketCryst *DTPK::prepareCrystPacket(size_t *size)
{
  vector<NeighborRecord> neighbors = this->crystDatabase.getListOfNeighbours();
  int numOfNeighbours = neighbors.size();
  printf("num of neighbours: %d\n", numOfNeighbours);
  *size = sizeof(DTPKPacketCryst) + sizeof(NeighborRecord) * numOfNeighbours;
  printf("size: %lu acctual size %lu additional size %lu\n", *size, sizeof(DTPKPacketCryst), sizeof(NeighborRecord) * numOfNeighbours);
  DTPKPacketCryst *packet = (DTPKPacketCryst *)malloc(*size);

  /*for (int i = 0; i < numOfNeighbours; i++)
  {
    packet->neighbors[i] = neighbors[i];
  }*/

  packet->type = CRYST;
  packet->id = this->packetCounter++;
  return packet;
}

void DTPK::sendCrystPacket()
{
  printf("Sending Cryst packet\n");

  size_t size = 0;
  DTPKPacketCryst *packet = this->prepareCrystPacket(&size);
  printf("size: %lu\n", size);
  
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, size, BROADCAST, 5000, random(200, this->Klimit * 1000));
}

void DTPK::sendNackPacket(uint16_t target, uint16_t id)
{
  RoutingRecord *routing = this->crystDatabase.getRouting(target);
  if(routing == nullptr){
    return;
  }

  DTPKPacketHeader *packet = (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  packet->type = NACK_NOTFOUND;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, sizeof(DTPKPacketHeader), routing->router, 5000, 0);
}
void DTPK::sendAckPacket(uint16_t target, uint16_t id)
{
  DTPKPacketHeader *packet = (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  packet->type = ACK;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, sizeof(DTPKPacketHeader), target, 5000, 0);
}

uint16_t DTPK::sendPacket(uint16_t target, unsigned char *packet, size_t size, int16_t timeout, bool isAck, PacketAckCallback callback)
{
  RoutingRecord *routing = this->crystDatabase.getRouting(target);
  if(routing == nullptr){
    callback(0, 0);
    return 0;
  }

  DTPKPacketGeneric *dtpkPacket = (DTPKPacketGeneric *)malloc(sizeof(DTPKPacketGeneric) + size);
  dtpkPacket->originalSender = MAC::getInstance()->getId();
  dtpkPacket->id = this->packetCounter++;
  dtpkPacket->type = DATA_SINGLE;
  dtpkPacket->finalTarget = target;
  memcpy(dtpkPacket->data, packet, size);

  this->addPacketToSendingQueue((DTPKPacketUnknown*)dtpkPacket, sizeof(DTPKPacketGeneric) + size, routing->router, timeout, 0, isAck, callback);
  return dtpkPacket->id;
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint16_t size)
{
  /*if(packet->mac.sender == 5){// only for testing purposes
      free(packet);
      return;
  }*/

  DTPKPacketUnknownReceive *dtpkPacket = (DTPKPacketUnknownReceive *)packet->data;
  //printf("received packet target: %d sende: %d type: %u size: %u\n",dtpkPacket->finalTarget, dtpkPacket->lcmm.mac.sender, dtpkPacket->type, size);
  DTPK::getInstance()->packetReceived.push(make_pair(dtpkPacket, size));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
  for(unsigned int i = 0; i < DTPK::getInstance()->packetWaiting.size(); i++){
    if(DTPK::getInstance()->packetWaiting[i].id == id){
      DTPK::getInstance()->packetWaiting[i].gotAck = true;
      DTPK::getInstance()->packetWaiting[i].success = success;
      return;
    }
  }
}