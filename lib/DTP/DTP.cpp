#include <DTP.h>
#include "mathextension.h"
DTP *DTP::dtp = nullptr;
DTPPacketNAPRecieve *DTP::neighborPacketToParse = nullptr;
bool DTP::neighborPacketWaiting = false;
uint16_t DTP::dtpPacketSize = 0;

DTP::DTP(uint16_t id, uint8_t NAPInterval)
{
    this->state = DTPStates::INITIAL_LISTENING;
    this->id = id;
    this->NAPInterval = NAPInterval;
    this->neighborPacketWaiting = false;
    this->lastNAPSsentInterval = 0;
    this->numOfIntervalsElapsed = 0;
    this->NAPPlaned = false;
    
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

bool DTP::checkIfCurrentPlanIsColliding()
{
    for (DTPNAPTimeRecord i : this->activeNeighbors)
    {
        if (i.startTime < this->myNAP.endTime && i.endTime > this->myNAP.endTime || i.startTime < this->myNAP.startTime && i.endTime > this->myNAP.startTime)
            return true;
    }
    return false;
}

void DTP::cleaningDeamon()
{
    Serial.println("Cleaning deamon");
    if (this->numOfIntervalsElapsed)
    {
        this->myNAP.endTime = this->myNAP.startTime + this->getTimeOnAirOfNAP();
        Serial.println("is collision eminent?: " + String(this->NAPPlaned));
        Serial.println(checkIfCurrentPlanIsColliding());
        this->NAPPlaned *= !checkIfCurrentPlanIsColliding();
    }

    // This code removes all neighbors who have not been heard from in the last two intervals. It also removes any routes that go through the removed neighbors.
    for (auto i : this->routingTable)
    {
        uint16_t idOfTarget = i.first;
        vector<DTPRoutingItem> routes = i.second;
        auto routeIterator = routes.begin();
        while (routeIterator != routes.end())
        {
            DTPRoutingItem route = *routeIterator;
            // todo by hashmap
            auto neighbourItterator = this->activeNeighbors.begin();
            while (neighbourItterator != this->activeNeighbors.end())
            {
                DTPNAPTimeRecord &neighbour = *neighbourItterator;
                if (neighbour.lastIntervalHeard - numOfIntervalsElapsed > 1)
                {
                    this->activeNeighbors.erase(neighbourItterator);
                    if (route.routingId == neighbour.id)
                        routes.erase(routeIterator);
                }
            }
        }

        if (routes.size() == 0)
            this->routingTable.erase(idOfTarget);
    }
}

void DTP::updateTime()
{
    uint32_t newTime = (millis() - timeOfInit) % (NAPInterval * 1000);
    bool timeSwitch = newTime < currentTime;
    this->NAPSend &= !timeSwitch;
    currentTime = newTime;
    if (timeSwitch)
        this->cleaningDeamon();
    this->numOfIntervalsElapsed += timeSwitch;
}

void DTP::parseNeigbours()
{
    if (!neighborPacketWaiting)
        return;

    Serial.println("Parsing neighbours");

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
    for (unsigned int i = 0; i < this->activeNeighbors.size(); i++)
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
    if (!alreadyInNeighbours)
        this->activeNeighbors.push_back((DTPNAPTimeRecord){senderId, startTime, endTime, this->numOfIntervalsElapsed});

    for (int i = 0; i < numOfNeighbors; i++)
    {
        auto found = this->routingTable.find(senderId);
        if (found != this->routingTable.end())
        {
            vector<DTPRoutingItem> &routes = (found->second);
            bool foundRoute = false;
            for (unsigned int j = 0; j < routes.size(); j++)
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

uint32_t DTP::getTimeOnAirOfNAP()
{
    uint16_t sizeOfRouting = this->routingTable.size() * sizeof(NeighborRecord) + sizeof(DTPPacketNAP) + LCMM_OVERHEAD;
    return MathExtension.timeOnAir(sizeOfRouting, 8, 9, 125.0, 7);
}

void DTP::NAPPlanRandom()
{
    Serial.println("planning ranodm");

    uint32_t timeOnAir = getTimeOnAirOfNAP();
    uint32_t minTime = currentTime + timeOnAir;
    uint32_t maxTime = this->NAPInterval * 1000 - timeOnAir * 1.3;
    uint32_t chosenTime = MathExtension.getRandomNumber(minTime, maxTime);
    this->myNAP.startTime = chosenTime;
    this->myNAP.endTime = chosenTime + timeOnAir;
    Serial.println("NAP planned for " + String(chosenTime) + " to " + String(chosenTime + timeOnAir));
    this->NAPPlaned = true;
}

DTPNAPTimeRecord DTP::getNearestTimeSlot(uint32_t ideal_min_time, uint32_t ideal_max_time, uint32_t min_start_time, uint32_t max_end_time)
{
    vector<DTPNAPTimeRecord> possibleTimeSlots;

    auto iterator = this->activeNeighbors.begin();

    while (iterator != this->activeNeighbors.end())
    {
        DTPNAPTimeRecord neighbour = *iterator;
        DTPNAPTimeRecord nextNeighbour = *(iterator + 1);
        if ((iterator + 1) == this->activeNeighbors.end())
            nextNeighbour = DTPNAPTimeRecord{0, max_end_time, max_end_time, 0};

        if (neighbour.endTime < ideal_min_time && nextNeighbour.startTime > ideal_max_time)
            possibleTimeSlots.push_back(DTPNAPTimeRecord{0, neighbour.endTime, nextNeighbour.startTime, 0});
    }

    if (possibleTimeSlots.size() == 0)
        return DTPNAPTimeRecord{0, 0, 0, 0};

    DTPNAPTimeRecord bestTimeSlot = possibleTimeSlots[0];

    for (DTPNAPTimeRecord i : possibleTimeSlots)
    {
        if (i.endTime - i.startTime < bestTimeSlot.endTime - bestTimeSlot.startTime)
            bestTimeSlot = i;
    }

    return bestTimeSlot;
}

void DTP::NAPPlanInteligent()
{
    uint32_t timeOnAir = getTimeOnAirOfNAP();

    uint32_t denominator = 0;
    uint32_t numerator = 0;

    for (DTPNAPTimeRecord i : this->activeNeighbors)
    {
        denominator += (i.endTime - i.startTime) * ((i.startTime - i.endTime) / 2);
        numerator += i.endTime - i.startTime;
    }

    float balancePoint = denominator / numerator;
    float idealPoint = this->NAPInterval * 1000 - balancePoint;

    if (this->currentTime > idealPoint - timeOnAir * 1.3 / 2)
        idealPoint += this->currentTime * 1.2 + timeOnAir * 1.3 / 2;

    uint32_t idealMinTime = idealPoint - timeOnAir * 1.3 / 2;
    uint32_t idealMaxTime = idealPoint + timeOnAir * 1.3 / 2;

    DTPNAPTimeRecord bestTimeSlot = getNearestTimeSlot(idealMinTime, idealMaxTime, 0, this->NAPInterval * 1000);

    if (bestTimeSlot.startTime == 0 && bestTimeSlot.endTime == 0)
    {
        this->NAPPlanRandom();
        return;
    }
    Serial.println("planning intelligent");
    this->NAPPlaned = true;
}

bool compareBydistance(const DTPRoutingItem &a, const DTPRoutingItem &b)
{
    return a.distance < b.distance;
}

void DTP::sendNAPPacket()
{
    Serial.println("Sending NAP packet");
    uint16_t sizeOfRouting = this->routingTable.size() * sizeof(NeighborRecord) + sizeof(DTPPacketNAP);

    DTPPacketNAP *packet = (DTPPacketNAP *)malloc(sizeOfRouting);

    packet->type = DTP_PACKET_TYPE_NAP;
    int i = 0;
    for (auto pair : this->routingTable)
    {
        vector<DTPRoutingItem> &routes = pair.second;
        packet->neighbors[i].id = pair.first;
        std::sort(routes.begin(), routes.end(), compareBydistance);
        if (routes.size() > 0)
        {
            packet->neighbors[i] = {routes[0].routingId, routes[0].distance};
        }
        else
        {
            Serial.println("No route to " + String(pair.first));
        }
    }

    LCMM::getInstance()->sendPacketSingle(false, 0, (unsigned char *)packet, sizeOfRouting, DTP::receiveAck);
    free(packet);
    this->lastNAPSsentInterval = this->numOfIntervalsElapsed;
    this->NAPSend = true;
}

void DTP::sendNAP()
{
    if (this->lastNAPSsentInterval == this->numOfIntervalsElapsed)
        return;
    if (!this->NAPPlaned && this->activeNeighbors.size() == 0){
        Serial.println("No neighbours");
        NAPPlanRandom();
    }

    if (!NAPPlaned){
        Serial.println("Planning intelligent");
        
        this->NAPPlanInteligent();
    }
    if (!NAPPlaned)
    {
        Serial.println("NAP not planed major issue");
        return;
    }

    if (NAPPlaned && !NAPSend && this->myNAP.startTime<this->currentTime &&this->myNAP.endTime> this->currentTime)
    {
        sendNAPPacket();
        Serial.println("Executing NAP transmission");
        return;
    }
}

void DTP::loop()
{
    updateTime();
    LCMM::getInstance()->loop();
    updateTime();
    sendNAP();

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
        DTP::dtpPacketSize = dtpSize;
        break;
    }
}

void DTP::receiveAck(uint16_t id, bool success)
{
    Serial.println("Ack received");
}
