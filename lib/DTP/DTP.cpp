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
        dtp = new DTP(id, NAPInterval);
    
}

void DTP::cleaningDeamon()
{
    for (auto i : this->routingTable)
    {
        uint16_t idOfTarget = i.first;
        vector<DTPRoutingItem> routes = i.second;
        auto routeIterator = routes.begin();
        while (routeIterator != routes.end()) 
        {   
            DTPRoutingItem route = *routeIterator;
            //todo by hashmap
            auto neighbourItterator = this->activeNeighbors.begin();
            while(neighbourItterator != this->activeNeighbors.end())
            {
                DTPNAPTimeRecord & neighbour = *neighbourItterator;
                if(neighbour.lastIntervalHeard - numOfIntervalsElapsed > 1){
                    this->activeNeighbors.erase(neighbourItterator);
                    if(route.routingId == neighbour.id)
                        routes.erase(routeIterator);
                }
            }
        }

        if(routes.size() == 0)
            this->routingTable.erase(idOfTarget);
    }
}

void DTP::updateTime()
{
    uint32_t newTime = (millis() - timeOfInit) % (NAPInterval * 1000);
    bool timeSwitch = newTime < currentTime;
    this->NAPPlaned &= !timeSwitch;
    currentTime = newTime;
    if(timeSwitch)
        this->cleaningDeamon();
    this->numOfIntervalsElapsed += timeSwitch;
}

void DTP::parseNeigbours()
{
    if (!neighborPacketWaiting)
        return;

    neighborPacketWaiting = false;

    DTPPacketNAPRecieve *packet = neighborPacketToParse;
    uint16_t size = dtpPacketSize;

    uint16_t numOfNeighbors = (size - sizeof(DTPPacketNAPRecieve)) / sizeof(NeighborRecord);
    NeighborRecord *neighbors = packet->neighbors;

    float timeOnAir = MathExtension.timeOnAir(size, 8, 9, 125, 7);
    uint32_t startTime = currentTime - timeOnAir * 1.05;
    uint32_t endTime = currentTime;

    uint16_t senderId = packet->lcmm.mac.sender;

    bool alreadyInNeighbours = false;
    for (int i = 0; i < this->activeNeighbors.size(); i++)
    {
        if (this->activeNeighbors[i].id == senderId)
        {
            this->activeNeighbors[i].startTime = startTime;
            this->activeNeighbors[i].endTime = endTime;
            this->activeNeighbors[i].lastIntervalHeard = this->numOfIntervalsElapsed;
            alreadyInNeighbours = true;
            break;
        }
    }
    if(!alreadyInNeighbours)
        this->activeNeighbors.push_back((DTPNAPTimeRecord){senderId, startTime, endTime, this->numOfIntervalsElapsed});
    

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
            if (!foundRoute)
                routes.push_back({neighbors[i].id, neighbors[i].distance});
        }
    }
}

void DTP::NAPPlanRandom()
{
    uint16_t sizeOfRouting = this->routingTable.size() * sizeof(NeighborRecord) + LCMM_OVERHEAD;
    uint16_t timeOnAir = MathExtension.timeOnAir(sizeOfRouting, 8, 9, 125.0, 7);
    uint32_t minTime = currentTime+timeOnAir;
    uint32_t maxTime = this->NAPInterval * 1000 - timeOnAir*1.3;
    MathExtension.setRandomRange(minTime, maxTime);
    uint32_t chosenTime = MathExtension.getRandomNumber();
    this->myNAP.startTime = chosenTime;
    this->myNAP.endTime = chosenTime + timeOnAir;
}

void DTP::sendNAP()
{
    if (this->lastNAPSsentInterval == this->numOfIntervalsElapsed)
        return;
    
    if(!this->NAPPlaned && this->activeNeighbors.size() == 0)
        NAPPlanRandom();
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
