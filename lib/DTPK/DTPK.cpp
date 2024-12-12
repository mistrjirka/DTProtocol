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
  this->_lastTick = 0;
  this->_crystTimeout.remaining = 0;
  this->_seed = MathExtension.murmur64((uint64_t)MAC::getInstance()->random()
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
      //Serial.println("time left to send: " + String(this->_packetRequests[i].timeLeftToSend) + " id: " + String(this->_packetRequests[i].packet->id));
      if (this->_packetRequests[i].timeLeftToSend <= 0 && LCMM::getInstance()->isSending() == false)
      {
        Serial.println("sending packet to " + String(this->_packetRequests[i].target) + " size: " + String(this->_packetRequests[i].size) + " type: " + String(this->_packetRequests[i].packet->type) + " id: " + String(this->_packetRequests[i].packet->id) + " isAck: " + String(this->_packetRequests[i].isAck) + " timeout: " + String(this->_packetRequests[i].timeout));

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
          printf("pusing to waiting\n");
        }

        if (this->_packetRequests[i].packet->type = CRYST)
        {
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
        this->_packetRequests[i].timeLeftToSend -= _currentTime - _lastTick;
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
      if (_packetWaiting[i].gotAck == true)
      {
        printf("calling success\n");
        printf("sending callback");
        if(this->_packetWaiting[i].callback)
          this->_packetWaiting[i].callback(1, this->_packetWaiting[i].timeout - this->_packetWaiting[i].timeLeft);
        this->_packetWaiting.erase(this->_packetWaiting.begin() + i);
      }
      else if (this->_packetWaiting[i].timeLeft <= 0)
      {
        if(this->_packetWaiting[i].callback)
          this->_packetWaiting[i].callback(0, 0);
        this->_packetWaiting.erase(this->_packetWaiting.begin() + i);\
        //find packet request and remove it FROM THE sending queue
        this->_packetRequests.erase(std::remove_if(this->_packetRequests.begin(), this->_packetRequests.end(), [this](DTPKPacketRequest &request) {
          return request.packet->id == this->_packetWaiting[i].id;
        }), this->_packetRequests.end());
        
      }
      else
      {
        this->_packetWaiting[i].timeLeft -= _currentTime - _lastTick;
      }
    }
  }
}

void DTPK::parseCrystPacket(pair<DTPKPacketUnknownReceive *, size_t> packet)
{
  printf("parsing packet\n");
  DTPKPacketCrystReceive *crystPacket = (DTPKPacketCrystReceive *)packet.first;
  size_t numberOfNeighbours = (packet.second - sizeof(DTPKPacketCrystReceive)) / sizeof(NeighborRecord);
  if (!this->_crystDatabase.isInCrystalizationSession())
  {
    this->_crystDatabase.startCrystalizationSession();
  }
  this->_crystTimeout.remaining = this->_Klimit * 1000;

  bool shouldSendCrystPacket = this->_crystDatabase.updateFromCrystPacket(crystPacket->lcmm.mac.sender, crystPacket->neighbors, numberOfNeighbours);

  if (shouldSendCrystPacket)
  {
    if (!this->_crystTimeout.sendingPacket)
      this->sendCrystPacket();
  }
}

void DTPK::parseSingleDataPacket(pair<DTPKPacketUnknownReceive *, size_t> packet)
{
  DTPKPacketGenericReceive *dataPacket = (DTPKPacketGenericReceive *)packet.first;

  if(dataPacket->finalTarget == MAC::getInstance()->getId() && dataPacket->originalSender == MAC::getInstance()->getId())
  {
    printf("loopback packet\n");
    return;
  }
  Serial.println("received packet");
  if(this->_recieveCallback)
    this->_recieveCallback(dataPacket, packet.second);

  this->sendAckPacket(dataPacket->originalSender, dataPacket->lcmm.mac.sender, dataPacket->id);
}

bool DTPK::isPacketForMe(DTPKPacketUnknownReceive *packet, size_t size)
{
  if (packet->lcmm.mac.target == BROADCAST){
    printf("packet is for broadcast\n");
    return true;
  }

  if (size < sizeof(DTPKPacketGenericReceive)){
    printf("packet is too small\n");
    return false;
  }

  DTPKPacketGenericReceive *genericPacket = (DTPKPacketGenericReceive *)packet;

  if (genericPacket->finalTarget == MAC::getInstance()->getId()){
    printf("packet is for me\n");
    return true;
  }

  // if the packet is not for me, check if it is for a neighbour
  RoutingRecord *routing = this->_crystDatabase.getRouting(genericPacket->finalTarget);

  if (routing == nullptr)
  {
    printf("routing is null\n");
    this->sendNackPacket(genericPacket->originalSender, packet->lcmm.mac.sender, genericPacket->id);
    return false;
  }

  printf("packet is for neighbour %d\n", routing->router);
  Serial.println("packet is for neighbour " + String(genericPacket->finalTarget) + " Through " + String(routing->router) );

  size_t sizeOfPacketToSend = size - sizeof(LCMMDataHeader);
  printf("original size %d packet size to send %d size of data part%d\n", size, sizeOfPacketToSend, sizeOfPacketToSend - sizeof(DTPKPacketUnknown));
  Serial.println("original size " + String(size) + " packet size to send " + String(sizeOfPacketToSend) + " size of data part" + String(sizeOfPacketToSend - sizeof(DTPKPacketUnknown)));

  DTPKPacketUnknown *dataPacket = (DTPKPacketUnknown *)malloc(sizeOfPacketToSend);
  dataPacket->type = packet->type;
  dataPacket->id = packet->id;
  memcpy(dataPacket->data, packet->data, sizeOfPacketToSend - sizeof(DTPKPacketUnknown));

  this->addPacketToSendingQueue(dataPacket, size, routing->router, 5000, 0);

  return false;
}

void DTPK::receivingDeamon()
{
  if (this->_packetReceived.size() > 0)
  {
    pair<DTPKPacketUnknownReceive *, size_t> packet = this->_packetReceived.front();
    this->_packetReceived.pop();
    DTPKPacketUnknownReceive *dtpkPacket = packet.first;

    printf("parsing packet sender: %hu type: %hhu id: %hu size: %u target: %hu lcmm type%hhu lcmm id: %hu\n",
           (unsigned int)dtpkPacket->lcmm.mac.sender, dtpkPacket->type, dtpkPacket->id, packet.second, dtpkPacket->lcmm.mac.target, dtpkPacket->lcmm.type, dtpkPacket->lcmm.id);

    if (!this->isPacketForMe(dtpkPacket, packet.second))
    {
      free(dtpkPacket);
      return;
    }

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
      printf("received ack\n");
      for (unsigned int i = 0; i < this->_packetWaiting.size(); i++)
      {
        if (this->_packetWaiting[i].id == packet.first->id)
        {
          this->_packetWaiting[i].gotAck = true;
          this->_packetWaiting[i].success = true;
        }
      }
      break;
    case NACK_NOTFOUND:
      printf("received nack\n");
      for (unsigned int i = 0; i < this->_packetWaiting.size(); i++)
      {
        if (this->_packetWaiting[i].id == packet.first->id)
        {
          this->_packetWaiting[i].gotAck = true;
          this->_packetWaiting[i].success = true;
        }
      }
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

void DTPK::crystDeamon()
{
  if (this->_crystTimeout.sendingPacket)
  {
    if (this->_crystTimeout.remainingTimeToSend <= 0)
    {
      this->_crystTimeout.sendingPacket = false;
      this->_crystTimeout.remainingTimeToSend = 0;
      size_t size = 0;
      DTPKPacketCryst *packet = this->prepareCrystPacket(&size);
      this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, size, BROADCAST, 5000, 0);
    }
    else
    {
      this->_crystTimeout.remainingTimeToSend -= _currentTime - _lastTick;
    }
  }
  if (this->_crystDatabase.isInCrystalizationSession())
  {
    if (this->_crystTimeout.remaining <= 0)
    {
      printf("ending crystalization session\n");

      bool updated = this->_crystDatabase.endCrystalizationSession();
      printf("timeout updated %d", updated);
      if (updated)
      {
        this->sendCrystPacket();
      }
    }
    else
    {
      this->_crystTimeout.remaining -= _currentTime - _lastTick;
    }
  }
}

void DTPK::loop()
{
  _currentTime = millis();

  this->receivingDeamon();
  this->sendingDeamon();
  this->timeoutDeamon();
  this->crystDeamon();

  LCMM::getInstance()->loop();

  _lastTick = _currentTime;
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
  if (this->_crystTimeout.sendingPacket)
  {
    return;
  }
  printf("sending cryst packet\n");
  this->_crystTimeout.sendingPacket = true;
  this->_crystTimeout.remainingTimeToSend = random(200, this->_Klimit * 1000);
}

void DTPK::sendNackPacket(uint16_t target, uint16_t from, uint16_t id)
{
  uint16_t whereToSend = from;

  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
  if(routing != nullptr)
  {
    //uncoment for faster routing whereToSend = routing->router;
  }
  
  DTPKPacketHeader *packet = (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  packet->type = NACK_NOTFOUND;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, sizeof(DTPKPacketHeader), whereToSend, 5000, true);
}
void DTPK::sendAckPacket(uint16_t target, uint16_t from, uint16_t id)
{
  DTPKPacketHeader *packet = (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  packet->type = ACK;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;

  uint16_t whereToSend = from;

  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
  if(routing != nullptr)
  {
    Serial.println("routing is not null");
    //uncoment for faster routing whereToSend = routing->router;
  }

  Serial.println("sending ack");

  Serial.println("sending ack to " + String(target) + " through " + String(whereToSend) + " originaly from "+ String(routing->originalRouter));
  this->addPacketToSendingQueue((DTPKPacketUnknown *)packet, sizeof(DTPKPacketHeader), whereToSend, 5000, 0, true);
}

uint16_t DTPK::sendPacket(uint16_t target, unsigned char *packet, size_t size, int16_t timeout, bool isAck, PacketAckCallback callback)
{
  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
  if (routing == nullptr)
  {
    callback(0, 0);
    return 0;
  }

  DTPKPacketGeneric *dtpkPacket = (DTPKPacketGeneric *)malloc(sizeof(DTPKPacketGeneric) + size);
  dtpkPacket->originalSender = MAC::getInstance()->getId();
  dtpkPacket->id = this->_packetCounter++;
  dtpkPacket->type = DATA_SINGLE;
  dtpkPacket->finalTarget = target;
  memcpy(dtpkPacket->data, packet, size);
  printf("sending packet to: %d through: %d\n", target, routing->router);

  this->addPacketToSendingQueue((DTPKPacketUnknown *)dtpkPacket, sizeof(DTPKPacketGeneric) + size, routing->router, timeout, 0, isAck, callback);
  return dtpkPacket->id;
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint16_t size)
{
  /*if(packet->mac.sender == 5){// only for testing purposes
      free(packet);
      return;
  }*/
  DTPKPacketUnknownReceive *dtpkPacket = (DTPKPacketUnknownReceive *)packet;
  DTPK::getInstance()->_packetReceived.push(make_pair(dtpkPacket, size));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
}