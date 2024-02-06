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

DTPK::DTPK(uint8_t Klimit) : _crystDatabase(MAC::getInstance()->getId())
{
  Serial.println(F("Initializing library DTPK..."));
  this->_crystTimeout.sendingPacket = false;
  this->_Klimit = Klimit;
  this->_packetCounter = 0;
  this->_timeOfInit = millis();
  this->_currentTime = _timeOfInit;
  this->lastTick = 0;
  this->_crystTimeout.remaining = 0;
  this->_seed = MathExtension.murmur64((uint64_t) MAC::getInstance()->random()
                                          << 32 |
                                      MAC::getInstance()->random());
  printf("Seed: %d\n", this->_seed);

  randomSeed(this->_seed);

  LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);

  MAC::getInstance()->setMode(RECEIVING, true);

  this->sendCrystPacket();
}

void DTPK::setPacketReceivedCallback(DTPK::PacketReceivedCallback callback)
{
 this->_recieveCallback = callback;
}

void DTPK::sendingDeamon()
{
  if (this->_packetRequests.size() > 0)
  {
    for (unsigned int i = 0; i < this->_packetRequests.size(); i++)
    {
      if (this->_packetRequests[i].timeLeftToSend <= 0)
      {
        Serial.println(F("sending packet"));

        printf("sending packet\n");
        LCMM::getInstance()->sendPacketSingle(
            this->_packetRequests[i].isAck,
            this->_packetRequests[i].target,
            (unsigned char *)this->_packetRequests[i].packet,
            this->_packetRequests[i].size,
            DTPK::receiveAck,
            this->_packetRequests[i].timeout);

        
        if (this->_packetRequests[i].isAck)
        {
          DTPKPacketWaiting waiting;
          waiting.id = this->_packetRequests[i].packet->id;
          waiting.timeout = this->_packetRequests[i].timeout;
          waiting.timeLeft = waiting.timeout;
          waiting.gotAck = false;
          waiting.success = false;
          waiting.callback = this->_packetRequests[i].callback;
          this->_packetWaiting.push_back(waiting);
        }

        if(this->_packetRequests[i].packet->type = CRYST){
          this->_crystTimeout.sendingPacket = false;
        }
        
        free(this->_packetRequests[i].packet);
        this->_packetRequests[i].packet = nullptr;

        this->_packetRequests.erase(this->_packetRequests.begin() + i);
      }
      else
      {
        Serial.println("time left to send: " + String(this->_packetRequests[i].timeLeftToSend));
        printf("time left to send: %d\n", this->_packetRequests[i].timeLeftToSend);
        this->_packetRequests[i].timeLeftToSend -= _currentTime - lastTick;
      }
    }
  }
}

void DTPK::timeoutDeamon()
{
  if (this->_packetWaiting.size() > 0)
  {
    for (unsigned int i = 0; i < this->_packetWaiting.size(); i++)
    {
      if(_packetWaiting[i].gotAck == true){
        this->_packetWaiting[i].callback(1, this->_packetWaiting[i].timeout - this->_packetWaiting[i].timeLeft);
        this->_packetWaiting.erase(this->_packetWaiting.begin() + i);
      }
      else
      if (this->_packetWaiting[i].timeLeft <= 0)
      {
        this->_packetWaiting[i].callback(0, 0);
        this->_packetWaiting.erase(this->_packetWaiting.begin() + i);
      }
      else
      {
        this->_packetWaiting[i].timeLeft -= _currentTime - lastTick;
      }
    }
  }
}

void DTPK::parseCrystPacket(pair<DTPKPacketUnknownReceive*, size_t> packet)
{
      printf("parsing packet\n");
      DTPKPacketCrystReceive *crystPacket = (DTPKPacketCrystReceive *)packet.first;
      size_t numberOfNeighbours = (packet.second - sizeof(DTPKPacketCrystReceive))/sizeof(NeighborRecord);
      if(!this->_crystDatabase.isInCrystalizationSession()){
        this->_crystDatabase.startCrystalizationSession();
      }
      this->_crystTimeout.remaining = this->_Klimit*1000;

      bool shouldSendCrystPacket = this->_crystDatabase.updateFromCrystPacket(crystPacket->lcmm.mac.sender, crystPacket->neighbors, numberOfNeighbours);
      
      if(shouldSendCrystPacket){
        if(!this->_crystTimeout.sendingPacket)
          this->sendCrystPacket();
      }
}

void DTPK::parseSingleDataPacket(pair<DTPKPacketUnknownReceive*, size_t> packet)
{
  DTPKPacketGenericReceive *dataPacket = (DTPKPacketGenericReceive *)packet.first;
  
  if (dataPacket->finalTarget == MAC::getInstance()->getId())
  {
    this->_recieveCallback(dataPacket, packet.second);
    this->sendAckPacket(dataPacket->originalSender, dataPacket->id);

  }else if(this->_crystDatabase.getRouting(dataPacket->finalTarget) != nullptr)
  {
    
    this->addPacketToSendingQueue((DTPKPacketUnknown *)dataPacket, packet.second, dataPacket->finalTarget, 5000, 0);
  }else
  {
    this->sendNackPacket(dataPacket->originalSender, dataPacket->id);
  }
}


void DTPK::receivingDeamon()
{
  if (this->_packetReceived.size() > 0)
  {
    pair<DTPKPacketUnknownReceive*, size_t> packet = this->_packetReceived.front();
    this->_packetReceived.pop();
    DTPKPacketUnknownReceive *dtpkPacket = packet.first;

    printf("parsing packet sender: %hu type: %hhu id: %hu size: %u target: %hu lcmm type%hhu lcmm id: %hu\n", 
    (unsigned int)dtpkPacket->lcmm.mac.sender, dtpkPacket->type, dtpkPacket->id, packet.second, dtpkPacket->lcmm.mac.target, dtpkPacket->lcmm.type, dtpkPacket->lcmm.id);

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
  return this->_crystDatabase.getListOfNeighbours();
}


void DTPK::crystTimeoutDeamon()
{
  if(this->_crystDatabase.isInCrystalizationSession())
  {
    if(this->_crystTimeout.remaining <= 0)
    {
      bool updated = this->_crystDatabase.endCrystalizationSession();
      if(updated)
      {
        this->sendCrystPacket();
      }
    }
    else
    {
      this->_crystTimeout.remaining -= _currentTime - lastTick;
    }
  }
}

void DTPK::loop()
{
  _currentTime = millis();

  this->receivingDeamon();
  this->sendingDeamon();
  this->timeoutDeamon();
  this->crystTimeoutDeamon();

  LCMM::getInstance()->loop();

  lastTick = _currentTime;

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
  this->_packetRequests.push_back(request);
}

DTPKPacketCryst *DTPK::prepareCrystPacket(size_t *size)
{
  vector<NeighborRecord> neighbors = this->_crystDatabase.getListOfNeighbours();
  int numOfNeighbours = neighbors.size();
  printf("num of neighbours: %d\n", numOfNeighbours);
  *size = sizeof(DTPKPacketCryst) + sizeof(NeighborRecord) * numOfNeighbours;
  printf("size: %lu acctual size %lu additional size %lu\n", *size, sizeof(DTPKPacketCryst), sizeof(NeighborRecord) * numOfNeighbours);
  DTPKPacketCryst *packet = (DTPKPacketCryst *)malloc(*size);

  for (int i = 0; i < numOfNeighbours; i++)
  {
    packet->neighbors[i] = neighbors[i];
  }

  packet->type = CRYST;
  packet->id = this->_packetCounter++;
  return packet;
}

void DTPK::sendCrystPacket()
{
  printf("Sending Cryst packet\n");
  this->_crystTimeout.sendingPacket = true;

  size_t size = 0;
  DTPKPacketCryst *packet = this->prepareCrystPacket(&size);
  printf("size: %lu\n", size);
  
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, size, BROADCAST, 5000, random(200, this->_Klimit * 1000));
}

void DTPK::sendNackPacket(uint16_t target, uint16_t id)
{
  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
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
  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
  if(routing == nullptr){
    callback(0, 0);
    return 0;
  }

  DTPKPacketGeneric *dtpkPacket = (DTPKPacketGeneric *)malloc(sizeof(DTPKPacketGeneric) + size);
  dtpkPacket->originalSender = MAC::getInstance()->getId();
  dtpkPacket->id = this->_packetCounter++;
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
  printf("original packet id: %hu\n",packet->id);
  DTPKPacketUnknownReceive *dtpkPacket = (DTPKPacketUnknownReceive *)packet;
  printf("received packet id: %hu\n",dtpkPacket->lcmm.id);
  DTPK::getInstance()->_packetReceived.push(make_pair(dtpkPacket, size));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
  for(unsigned int i = 0; i < DTPK::getInstance()->_packetWaiting.size(); i++){
    if(DTPK::getInstance()->_packetWaiting[i].id == id){
      DTPK::getInstance()->_packetWaiting[i].gotAck = true;
      DTPK::getInstance()->_packetWaiting[i].success = success;
      return;
    }
  }
}