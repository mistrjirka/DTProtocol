#include <DTP.h>
DTP *DTP::dtp = nullptr;

DTP::DTP(uint16_t id, uint8_t NAPInterval)
{
    this->state = DTPStates::INITIAL_LISTENING;
    this->id = id;
    this->NAPInterval = NAPInterval;
    this->neighborPacketWaiting = false;
    this->lastNAPSsentInterval = 0;
    this->numOfIntervalsElapsed = 0;
    LCMM::initialize(DTP::receivePacket, DTP::receiveAck);
    timeOfInit = millis();
    currentTime = 0;
}

DTP *DTP::getInstance()
{
    if (!dtp)
    {
        return nullptr;
    }
    return dtp;
}

void DTP::initialize(uint16_t id, uint8_t NAPInterval)
{
    if (dtp == nullptr)
    {
        dtp = new DTP(id, NAPInterval);
    }
}

void DTP::updateTime()
{
    uint32_t newTime = (millis() - timeOfInit) % (NAPInterval * 1000);
    this->numOfIntervalsElapsed += newTime < currentTime;
    currentTime = newTime;
}

void DTP::parseNeigbours()
{
    if (!neighborPacketWaiting)
    {
        return;
    }

    neighborPacketWaiting = false;

    DTPPacketNAPRecieve *packet = neighborPacketToParse;
    uint16_t size = dtpPacketSize;

    uint16_t numOfNeighbors = (size - sizeof(DTPPacketNAPRecieve)) / sizeof(NeighborRecord);
    NeighborRecord *neighbors = packet->neighbors;

    float timeOnAir = MathExtension.timeOnAir(size, 8, 9, 125, 7);
    uint32_t startTime = currentTime - timeOnAir * 1.05;
    uint32_t endTime = currentTime;

    uint16_t senderId = packet->lcmm.mac.sender;

    for (int i = 0; i < this->activeNeighbors.size(); i++)
    {
        if (this->activeNeighbors[i].id == senderId)
        {
            this->activeNeighbors[i].startTime = startTime;
            this->activeNeighbors[i].endTime = endTime;
            this->activeNeighbors[i].lastIntervalHeard = this->numOfIntervalsElapsed;
            break;
        }
    }

    for (int i = 0; i < numOfNeighbors; i++)
    {
        auto found = this->routingTable.find(senderId);
        if (found != this->routingTable.end())
        {
            vector<DTPRoutingItem> &routes = (found->second);
            bool foundRoute = false;
            for (int j = 0; j < routes.size(); j++)
            {
                if (routes[j].routingId == senderId)
                {
                    routes[j].distance = neighbors[i].distance;
                    foundRoute = true;
                    break;
                }
            }
            routes.push_back({neighbors[i].id, neighbors[i].distance});
        }
    }
}

void DTP::loop()
{
    updateTime();
    LCMM::getInstance()->loop();
    updateTime();

    // Implement your loop logic here
}

DTPStates DTP::getState()
{
    return this->state;
}

void DTP::receivePacket(LCMMPacketDataRecieve *packet, uint16_t size)
{
    DTPPacketUnkown *dtpPacket = (DTPPacketUnkown *)packet->data;
    uint16_t dtpSize = size - sizeof(LCMMPacketDataRecieve);
    switch (dtpPacket->type)
    {
    case DTP_PACKET_TYPE_NAP:
        DTP::neighborPacketWaiting = true;
        DTP::neighborPacketToParse = (DTPPacketNAPRecieve *)packet;
        break;
    }
}

void DTP::receiveAck(uint16_t id, bool success)
{
}
