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
  return dtpk;
}

DTPK::DTPK(uint8_t Klimit) : _crystDatabase(MAC::getInstance()->getId())
{
  Serial.println(F("Initializing library DTPK..."));
  this->_Klimit = Klimit;
  this->_packetCounter = 0;
  this->_timeOfInit = millis();
  this->_currentTime = this->_timeOfInit;
  this->_lastTick = this->_currentTime;
  this->_crystTimeout.remaining = 0;
  this->_crystTimeout.sendingPacket = false;
  this->_crystTimeout.remainingTimeToSend = 0;

  this->_seed = MathExtension.murmur64(
      ((uint64_t)MAC::getInstance()->random() << 32) |
      MAC::getInstance()->random());
  randomSeed(this->_seed);

  LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);
  MAC::getInstance()->setMode(RECEIVING, true);

  this->_waitingForAck = false;
  this->_currentlySendingId = 0;
  this->sendCrystPacket();
}

void DTPK::setPacketReceivedCallback(DTPK::PacketReceivedCallback callback)
{
  this->_recieveCallback = callback;
}

bool DTPK::hasSeenData(uint16_t originalSender, uint16_t id) const
{
  for (const PacketIdentity &entry : this->_recentData)
  {
    if (entry.valid && entry.originalSender == originalSender && entry.id == id)
      return true;
  }
  return false;
}

void DTPK::rememberData(uint16_t originalSender, uint16_t id)
{
  if (this->hasSeenData(originalSender, id))
    return;

  PacketIdentity &entry = this->_recentData[this->_recentDataNext];
  entry.originalSender = originalSender;
  entry.id = id;
  entry.valid = true;
  this->_recentDataNext =
      (this->_recentDataNext + 1) % RECENT_DATA_CACHE_SIZE;
}

void DTPK::sendingDeamon()
{
  if (this->_packetRequests.empty())
    return;

  const uint32_t elapsed = this->_currentTime - this->_lastTick;

  for (size_t i = 0; i < this->_packetRequests.size(); ++i)
  {
    DTPKPacketRequest &request = this->_packetRequests[i];

    if (request.timeLeftToSend > 0)
    {
      request.timeLeftToSend =
          elapsed >= (uint32_t)request.timeLeftToSend
              ? 0
              : request.timeLeftToSend - (int32_t)elapsed;
    }

    const DTPKPacketType type = request.packet->type;
    const bool controlPacket =
        type == ACK || type == NACK_NOTFOUND || type == CRYST;

    if (request.timeLeftToSend > 0 ||
        LCMM::getInstance()->isSending() ||
        (this->_waitingForAck && !controlPacket))
    {
      continue;
    }

    if (request.size > DATASIZE_LCMM)
    {
      if (request.callback)
        request.callback(0, 0);
      if (type == CRYST)
        this->_crystTimeout.sendingPacket = false;
      free(request.packet);
      this->_packetRequests.erase(this->_packetRequests.begin() + i);
      return;
    }

    const uint16_t lcmmId = LCMM::getInstance()->sendPacketSingle(
        request.lcmmAck,
        request.target,
        (unsigned char *)request.packet,
        (uint8_t)request.size,
        DTPK::receiveAck,
        request.timeout > 0 ? (uint32_t)(request.timeout / 3) : 1,
        3);

    if (lcmmId == 0)
      return;

    if (request.dtpkAck)
    {
      DTPKPacketWaiting waiting;
      waiting.id = request.packet->id;
      waiting.timeout = request.timeout > 0 ? (uint32_t)request.timeout : 1;
      waiting.timeLeft = request.timeout > 0 ? request.timeout : 1;
      waiting.gotAck = false;
      waiting.success = false;
      waiting.callback = request.callback;
      this->_packetWaiting.push_back(waiting);
      this->_waitingForAck = true;
      this->_currentlySendingId = request.packet->id;
    }

    if (type == CRYST)
    {
      this->_crystTimeout.sendingPacket = false;
      this->_crystTimeout.remainingTimeToSend = 0;
    }

    free(request.packet);
    this->_packetRequests.erase(this->_packetRequests.begin() + i);
    return;
  }
}

void DTPK::timeoutDeamon()
{
  if (this->_packetWaiting.empty())
    return;

  const uint32_t elapsed = this->_currentTime - this->_lastTick;
  vector<size_t> toDelete;

  for (size_t i = 0; i < this->_packetWaiting.size(); ++i)
  {
    DTPKPacketWaiting &waiting = this->_packetWaiting[i];

    if (waiting.gotAck)
    {
      if (waiting.callback)
      {
        const uint16_t ping = waiting.success
            ? (uint16_t)(waiting.timeout -
                         (uint32_t)std::max<int32_t>(waiting.timeLeft, 0))
            : 0;
        waiting.callback(waiting.success ? 1 : 0, ping);
      }

      if (this->_currentlySendingId == waiting.id)
      {
        this->_waitingForAck = false;
        this->_currentlySendingId = 0;
      }
      toDelete.push_back(i);
      continue;
    }

    if (waiting.timeLeft <= 0)
    {
      if (waiting.callback)
        waiting.callback(0, 0);

      if (this->_currentlySendingId == waiting.id)
      {
        this->_waitingForAck = false;
        this->_currentlySendingId = 0;
      }

      const uint16_t id = waiting.id;
      auto newEnd = std::remove_if(
          this->_packetRequests.begin(),
          this->_packetRequests.end(),
          [id](DTPKPacketRequest &request) {
            if (request.packet && request.packet->id == id)
            {
              free(request.packet);
              request.packet = nullptr;
              return true;
            }
            return false;
          });
      this->_packetRequests.erase(newEnd, this->_packetRequests.end());
      toDelete.push_back(i);
      continue;
    }

    waiting.timeLeft =
        elapsed >= (uint32_t)waiting.timeLeft
            ? 0
            : waiting.timeLeft - (int32_t)elapsed;
  }

  for (auto it = toDelete.rbegin(); it != toDelete.rend(); ++it)
    this->_packetWaiting.erase(this->_packetWaiting.begin() + *it);
}

void DTPK::parseCrystPacket(pair<DTPKPacketUnknownReceive *, size_t> packet)
{
  if (packet.second < sizeof(DTPKPacketCrystReceive))
    return;

  DTPKPacketCrystReceive *crystPacket =
      (DTPKPacketCrystReceive *)packet.first;
  const size_t numberOfNeighbours =
      (packet.second - sizeof(DTPKPacketCrystReceive)) / sizeof(NeighborRecord);

  if (!this->_crystDatabase.isInCrystalizationSession())
    this->_crystDatabase.startCrystalizationSession();

  this->_crystTimeout.remaining = (int32_t)this->_Klimit * 1000;

  const bool shouldSendCrystPacket = this->_crystDatabase.updateFromCrystPacket(
      crystPacket->lcmm.mac.sender,
      crystPacket->neighbors,
      (int)numberOfNeighbours);

  if (shouldSendCrystPacket && !this->_crystTimeout.sendingPacket)
    this->sendCrystPacket();
}

void DTPK::parseSingleDataPacket(pair<DTPKPacketUnknownReceive *, size_t> packet)
{
  if (packet.second < sizeof(DTPKPacketGenericReceive))
    return;

  DTPKPacketGenericReceive *dataPacket =
      (DTPKPacketGenericReceive *)packet.first;

  if (dataPacket->finalTarget == MAC::getInstance()->getId() &&
      dataPacket->originalSender == MAC::getInstance()->getId())
  {
    return;
  }

  const bool duplicate =
      this->hasSeenData(dataPacket->originalSender, dataPacket->id);

  if (!duplicate)
  {
    this->rememberData(dataPacket->originalSender, dataPacket->id);
    if (this->_recieveCallback)
      this->_recieveCallback(dataPacket, (uint16_t)packet.second);
  }

  // A retransmission caused by a lost lower-layer ACK must not be delivered to
  // the application twice, but the end-to-end ACK is intentionally repeated so
  // its source can still complete after losing the previous response.
  if ((dataPacket->flags & DTPK_FLAG_E2E_ACK_REQUESTED) != 0)
  {
    this->sendAckPacket(
        dataPacket->originalSender,
        dataPacket->lcmm.mac.sender,
        dataPacket->id);
  }
}

bool DTPK::isPacketForMe(DTPKPacketUnknownReceive *packet, size_t size)
{
  if (size < sizeof(DTPKPacketUnknownReceive))
    return false;

  if (packet->lcmm.mac.target == BROADCAST)
    return true;

  if (size < sizeof(DTPKPacketGenericReceive))
    return false;

  DTPKPacketGenericReceive *genericPacket =
      (DTPKPacketGenericReceive *)packet;

  if (genericPacket->finalTarget == MAC::getInstance()->getId())
    return true;

  // A routed packet may arrive with hopLimit==1, but it may not be forwarded
  // again. DATA gets a useful NACK; ACK/NACK simply expire.
  if (genericPacket->hopLimit <= 1)
  {
    if (genericPacket->type == DATA_SINGLE)
    {
      this->sendNackPacket(
          genericPacket->originalSender,
          packet->lcmm.mac.sender,
          genericPacket->id);
    }
    return false;
  }

  RoutingRecord *routing =
      this->_crystDatabase.getRouting(genericPacket->finalTarget);

  if (routing == nullptr)
  {
    if (genericPacket->type == DATA_SINGLE)
    {
      this->sendNackPacket(
          genericPacket->originalSender,
          packet->lcmm.mac.sender,
          genericPacket->id);
    }
    return false;
  }

  // Link-level retry can present the same DATA to DTPK more than once. The LCMM
  // ACK has already been sent, so an intermediate node can safely drop the
  // duplicate here rather than forwarding it twice.
  if (genericPacket->type == DATA_SINGLE &&
      this->hasSeenData(genericPacket->originalSender, genericPacket->id))
  {
    return false;
  }

  const size_t sizeOfPacketToSend = size - sizeof(LCMMDataHeader);
  if (sizeOfPacketToSend < sizeof(DTPKPacketHeader) ||
      sizeOfPacketToSend > DATASIZE_LCMM)
  {
    if (genericPacket->type == DATA_SINGLE)
    {
      this->sendNackPacket(
          genericPacket->originalSender,
          packet->lcmm.mac.sender,
          genericPacket->id);
    }
    return false;
  }

  DTPKPacketUnknown *forwarded =
      (DTPKPacketUnknown *)malloc(sizeOfPacketToSend);
  if (!forwarded)
    return false;

  memcpy(
      forwarded,
      ((unsigned char *)packet) + sizeof(LCMMDataHeader),
      sizeOfPacketToSend);

  DTPKPacketGeneric *forwardedGeneric = (DTPKPacketGeneric *)forwarded;
  --forwardedGeneric->hopLimit;

  if (genericPacket->type == DATA_SINGLE)
    this->rememberData(genericPacket->originalSender, genericPacket->id);

  this->addPacketToSendingQueue(
      forwarded,
      sizeOfPacketToSend,
      routing->router,
      5000,
      0,
      true,
      false);

  return false;
}

void DTPK::receivingDeamon()
{
  if (this->_packetReceived.empty())
    return;

  pair<DTPKPacketUnknownReceive *, size_t> packet =
      this->_packetReceived.front();
  this->_packetReceived.pop();

  DTPKPacketUnknownReceive *dtpkPacket = packet.first;
  if (!dtpkPacket || packet.second < sizeof(DTPKPacketUnknownReceive))
  {
    free(dtpkPacket);
    return;
  }

  if (!this->isPacketForMe(dtpkPacket, packet.second))
  {
    free(dtpkPacket);
    return;
  }

  switch (dtpkPacket->type)
  {
  case CRYST:
    this->parseCrystPacket(packet);
    break;

  case DATA_SINGLE:
    this->parseSingleDataPacket(packet);
    break;

  case ACK:
  case NACK_NOTFOUND:
  {
    const bool success = dtpkPacket->type == ACK;

    if (this->_waitingForAck &&
        this->_currentlySendingId == dtpkPacket->id)
    {
      this->_waitingForAck = false;
      this->_currentlySendingId = 0;
    }

    for (DTPKPacketWaiting &waiting : this->_packetWaiting)
    {
      if (waiting.id == dtpkPacket->id)
      {
        waiting.gotAck = true;
        waiting.success = success;
      }
    }
    break;
  }

  default:
    break;
  }

  free(dtpkPacket);
}

vector<NeighborRecord> DTPK::getNeighbours()
{
  return this->_crystDatabase.getListOfNeighbours();
}

void DTPK::crystDeamon()
{
  const uint32_t elapsed = this->_currentTime - this->_lastTick;

  if (this->_crystTimeout.sendingPacket)
  {
    if (this->_crystTimeout.remainingTimeToSend == -1)
    {
      // A CRYST is already queued and clears sendingPacket when handed to LCMM.
    }
    else if (this->_crystTimeout.remainingTimeToSend <= 0)
    {
      size_t size = 0;
      DTPKPacketCryst *packet = this->prepareCrystPacket(&size);
      if (packet)
      {
        this->_crystTimeout.remainingTimeToSend = -1;
        this->addPacketToSendingQueue(
            (DTPKPacketUnknown *)packet,
            size,
            BROADCAST,
            5000,
            0,
            false,
            false);
      }
      else
      {
        this->_crystTimeout.sendingPacket = false;
      }
    }
    else
    {
      this->_crystTimeout.remainingTimeToSend =
          elapsed >= (uint32_t)this->_crystTimeout.remainingTimeToSend
              ? 0
              : this->_crystTimeout.remainingTimeToSend - (int32_t)elapsed;
    }
  }

  if (this->_crystDatabase.isInCrystalizationSession())
  {
    if (this->_crystTimeout.remaining <= 0)
    {
      const bool updated = this->_crystDatabase.endCrystalizationSession();
      if (updated)
        this->sendCrystPacket();
    }
    else
    {
      this->_crystTimeout.remaining =
          elapsed >= (uint32_t)this->_crystTimeout.remaining
              ? 0
              : this->_crystTimeout.remaining - (int32_t)elapsed;
    }
  }
}

void DTPK::loop()
{
  this->_currentTime = millis();

  this->receivingDeamon();
  this->sendingDeamon();
  this->timeoutDeamon();
  this->crystDeamon();

  LCMM::getInstance()->loop();

  this->_lastTick = this->_currentTime;
}

void DTPK::addPacketToSendingQueue(
    DTPKPacketUnknown *packet,
    size_t size,
    uint16_t target,
    int16_t timeout,
    int16_t timeLeftToSend,
    bool lcmmAck,
    bool dtpkAck,
    PacketAckCallback callback)
{
  if (!packet)
    return;

  if (size > DATASIZE_LCMM)
  {
    if (packet->type == CRYST)
      this->_crystTimeout.sendingPacket = false;
    if (callback)
      callback(0, 0);
    free(packet);
    return;
  }

  DTPKPacketRequest request;
  request.packet = packet;
  request.size = size;
  request.timeout = timeout;
  request.target = target;
  request.timeLeftToSend = timeLeftToSend;
  request.lcmmAck = lcmmAck;
  request.dtpkAck = dtpkAck;
  request.callback = callback;

  if (packet->type == ACK || packet->type == NACK_NOTFOUND)
    this->_packetRequests.insert(this->_packetRequests.begin(), request);
  else
    this->_packetRequests.push_back(request);
}

void DTPK::sendPacketToTarget(
    DTPKPacketUnknown *packet,
    size_t size,
    uint16_t target,
    int16_t timeout,
    bool dtpkAck)
{
  this->addPacketToSendingQueue(
      packet, size, target, timeout, 0, true, dtpkAck);
}

DTPKPacketCryst *DTPK::prepareCrystPacket(size_t *size)
{
  vector<NeighborRecord> neighbors = this->_crystDatabase.getListOfNeighbours();
  const size_t required =
      sizeof(DTPKPacketCryst) + sizeof(NeighborRecord) * neighbors.size();

  if (required > DATASIZE_LCMM)
  {
    *size = 0;
    return nullptr;
  }

  DTPKPacketCryst *packet =
      (DTPKPacketCryst *)malloc(required);
  if (!packet)
  {
    *size = 0;
    return nullptr;
  }

  for (size_t i = 0; i < neighbors.size(); ++i)
    packet->neighbors[i] = neighbors[i];

  packet->type = CRYST;
  packet->id = this->_packetCounter++;
  *size = required;
  return packet;
}

void DTPK::sendCrystPacket()
{
  if (this->_crystTimeout.sendingPacket)
    return;

  this->_crystTimeout.sendingPacket = true;
  const long upper = std::max<long>(201, (long)this->_Klimit * 1000);
  this->_crystTimeout.remainingTimeToSend = (int32_t)random(200, upper);
}

void DTPK::sendNackPacket(uint16_t target, uint16_t from, uint16_t id)
{
  DTPKPacketHeader *packet =
      (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  if (!packet)
    return;

  packet->type = NACK_NOTFOUND;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;
  packet->flags = DTPK_FLAG_NONE;
  packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

  this->addPacketToSendingQueue(
      (DTPKPacketUnknown *)packet,
      sizeof(DTPKPacketHeader),
      from,
      5000,
      0,
      true,
      false);
}

void DTPK::sendAckPacket(uint16_t target, uint16_t from, uint16_t id)
{
  DTPKPacketHeader *packet =
      (DTPKPacketHeader *)malloc(sizeof(DTPKPacketHeader));
  if (!packet)
    return;

  packet->type = ACK;
  packet->originalSender = MAC::getInstance()->getId();
  packet->finalTarget = target;
  packet->id = id;
  packet->flags = DTPK_FLAG_NONE;
  packet->hopLimit = DTPK_DEFAULT_HOP_LIMIT;

  this->addPacketToSendingQueue(
      (DTPKPacketUnknown *)packet,
      sizeof(DTPKPacketHeader),
      from,
      5000,
      0,
      true,
      false);
}

uint16_t DTPK::sendPacket(
    uint16_t target,
    unsigned char *packet,
    size_t size,
    int16_t timeout,
    bool dtpkAck,
    PacketAckCallback callback)
{
  RoutingRecord *routing = this->_crystDatabase.getRouting(target);
  if (routing == nullptr)
  {
    if (callback)
      callback(0, 0);
    return 0;
  }

  const size_t wireSize = sizeof(DTPKPacketGeneric) + size;
  if (wireSize > DATASIZE_LCMM)
  {
    if (callback)
      callback(0, 0);
    return 0;
  }

  DTPKPacketGeneric *dtpkPacket =
      (DTPKPacketGeneric *)malloc(wireSize);
  if (!dtpkPacket)
  {
    if (callback)
      callback(0, 0);
    return 0;
  }

  dtpkPacket->originalSender = MAC::getInstance()->getId();
  dtpkPacket->id = this->_packetCounter++;
  dtpkPacket->type = DATA_SINGLE;
  dtpkPacket->finalTarget = target;
  dtpkPacket->flags = dtpkAck ? DTPK_FLAG_E2E_ACK_REQUESTED : DTPK_FLAG_NONE;
  dtpkPacket->hopLimit = DTPK_DEFAULT_HOP_LIMIT;
  if (size > 0)
    memcpy(dtpkPacket->data, packet, size);

  const uint16_t id = dtpkPacket->id;
  this->rememberData(dtpkPacket->originalSender, id);

  this->addPacketToSendingQueue(
      (DTPKPacketUnknown *)dtpkPacket,
      wireSize,
      routing->router,
      timeout,
      0,
      true,
      dtpkAck,
      callback);

  return id;
}

void DTPK::receivePacket(LCMMPacketDataReceive *packet, uint16_t size)
{
  if (!packet)
    return;

  DTPKPacketUnknownReceive *dtpkPacket =
      (DTPKPacketUnknownReceive *)packet;
  DTPK::getInstance()->_packetReceived.push(make_pair(dtpkPacket, size));
}

void DTPK::receiveAck(uint16_t id, bool success)
{
  (void)id;
  (void)success;
}
