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
    this->NAPSend = false;
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
        Serial.println("Going throught routing table");

        uint16_t idOfTarget = i.first;
        vector<DTPRoutingItem> routes = i.second;
        auto routeIterator = routes.begin();
        while (routeIterator != routes.end())
        {
            Serial.println("Going throught routes");

            DTPRoutingItem route = *routeIterator;
            // todo by hashmap
            auto neighbourItterator = this->activeNeighbors.begin();
            bool routeIteratorRemoved = false;
            while (neighbourItterator != this->activeNeighbors.end())
            {
                DTPNAPTimeRecord &neighbour = *neighbourItterator;
                if (neighbour.lastIntervalHeard - numOfIntervalsElapsed > 1)
                {
                    this->activeNeighbors.erase(neighbourItterator);
                    if (route.routingId == neighbour.id && !routeIteratorRemoved)
                    {
                        routes.erase(routeIterator);
                        routeIteratorRemoved = true;
                    }
                }
                else
                {
                    neighbourItterator++;
                }
            }
            if (!routeIteratorRemoved)
                routeIterator++;
        }

        if (routes.size() == 0)
            this->routingTable.erase(idOfTarget);
    }
}

void DTP::updateTime()
{
    uint32_t newTime = (millis() - timeOfInit) % (NAPInterval * 1000);
    bool timeSwitch = newTime < currentTime;
    currentTime = newTime;
    if (timeSwitch)
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
    uint16_t numOfNeighbors = (size - sizeof(DTPPacketNAP)) / sizeof(NeighborRecord);
    Serial.println(size);
    Serial.println(sizeof(DTPPacketNAP));
    Serial.println(sizeof(NeighborRecord));
    Serial.println((size - sizeof(DTPPacketNAP)));
    NeighborRecord *neighbors = packet->neighbors;
    Serial.println("Parsing neighbours" + String(numOfNeighbors));

    float timeOnAir = MathExtension.timeOnAir(size, 8, 9, 125, 7);
    uint32_t startTime = currentTime - timeOnAir * 1.05;
    uint32_t endTime = currentTime;

    uint16_t senderId = packet->lcmm.mac.sender;

    bool alreadyInNeighbours = false;
    for (unsigned int i = 0; i < this->activeNeighbors.size(); i++)
    {
        Serial.println("Going thtough neihbours");

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
    {
        Serial.println("pushing into neighbours");

        this->activeNeighbors.push_back((DTPNAPTimeRecord){senderId, startTime, endTime, this->numOfIntervalsElapsed});
    }

    auto foundSender = this->routingTable.find(senderId);

    if (foundSender == this->routingTable.end())
    {
        Serial.println("inserting into routing table");
        this->routingTable.insert({senderId, vector<DTPRoutingItem>({(DTPRoutingItem){senderId, 1}})});
    }
    else
    {
        Serial.println("found sender");
        vector<DTPRoutingItem> &routes = foundSender->second;
        bool foundRoute = false;
        for (unsigned int j = 0; j < routes.size(); j++)
        {
            if (routes[j].routingId == senderId)
            {
                routes[j].distance = 1;
                foundRoute = true;
                break;
            }
        }
        if (!foundRoute)
            routes.push_back((DTPRoutingItem){senderId, 1});
    }

    if (this->neighborPacketToParse)
    {
        free(this->neighborPacketToParse);
        this->neighborPacketToParse = nullptr;
    }

    for (int i = 0; i < numOfNeighbors; i++)
    {
        Serial.println("going through neighbours");

        if (neighbors[i].id == this->id)
        {
            continue;
        }
        auto found = this->routingTable.find(neighbors[i].id);
        Serial.println("after finding");
        if (found != this->routingTable.end())
        {
            Serial.println("found something");

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
                routes.push_back((DTPRoutingItem){senderId, (uint8_t)(neighbors[i].distance + 1)});
        }
    }
    Serial.println("end of parsing");
    for (auto i : this->routingTable)
    {
        Serial.println("routing table");
        Serial.println(i.first);
        for (auto j : i.second)
        {
            Serial.println(j.routingId);
            Serial.println(j.distance);
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

        if (nextNeighbour.startTime - neighbour.endTime > ideal_max_time - ideal_min_time)
        {
            possibleTimeSlots.push_back(DTPNAPTimeRecord{0, neighbour.endTime, nextNeighbour.startTime, 0});
        }
        iterator++;
    }

    if (possibleTimeSlots.size() == 0)
        return DTPNAPTimeRecord{0, 0, 0, 0};

    DTPNAPTimeRecord bestTimeSlot = possibleTimeSlots[0];

    for (DTPNAPTimeRecord i : possibleTimeSlots)
    {
        if (i.endTime - i.startTime < bestTimeSlot.endTime - bestTimeSlot.startTime)
            bestTimeSlot = i;
    }

    int howMuchCanFit = (bestTimeSlot.endTime - bestTimeSlot.startTime)/(ideal_max_time - ideal_min_time);
    if(howMuchCanFit > 1){
        int margin = (bestTimeSlot.endTime - bestTimeSlot.startTime) - howMuchCanFit * (ideal_max_time - ideal_min_time);
        int sideMargin = margin/(howMuchCanFit*2);
        int timeSlotChosen = MathExtension.getRandomNumber(0, howMuchCanFit-1);
        bestTimeSlot.startTime = bestTimeSlot.startTime +  timeSlotChosen * (sideMargin + ideal_max_time - ideal_min_time);
        bestTimeSlot.endTime = bestTimeSlot.startTime + (ideal_max_time - ideal_min_time);
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
        Serial.println("going through neighbours");
        Serial.println((i.endTime - i.startTime));
        Serial.println((i.startTime + (i.startTime - i.endTime) / 2));
        denominator += (i.endTime - i.startTime) * (i.startTime + (i.startTime - i.endTime) / 2);
        numerator += i.endTime - i.startTime;
    }

    float balancePoint = denominator / numerator;
    float idealPoint = this->NAPInterval * 1000 - balancePoint;
    Serial.println("ideal point: " + String(balancePoint));

    if (this->currentTime > idealPoint - timeOnAir * 1.3 / 2)
    {
        Serial.println(String(this->currentTime) + " is over " + String(timeOnAir * 1.3 / 2));

        idealPoint += this->currentTime * 1.2 + timeOnAir * 1.3 / 2;
    }

    uint32_t idealMinTime = idealPoint - timeOnAir * 1.3 / 2;
    uint32_t idealMaxTime = idealPoint + timeOnAir * 1.3 / 2;

    DTPNAPTimeRecord bestTimeSlot = getNearestTimeSlot(idealMinTime, idealMaxTime, 0, this->NAPInterval * 1000);

    if (bestTimeSlot.startTime == 0 && bestTimeSlot.endTime == 0)
    {
        this->NAPPlanRandom();
        return;
    }
    Serial.println("planning intelligent" + String(bestTimeSlot.startTime) + " to " + String(bestTimeSlot.endTime));
    this->myNAP.startTime = bestTimeSlot.startTime;
    this->myNAP.endTime = bestTimeSlot.endTime;
    this->NAPPlaned = true;
    this->NAPSend = false;
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
    if (!this->NAPPlaned && this->activeNeighbors.size() == 0)
    {
        Serial.println("No neighbours");
        NAPPlanRandom();
    }

    if (!NAPPlaned)
    {
        Serial.println("Planning intelligent");

        this->NAPPlanInteligent();
    }
    if (!NAPPlaned)
    {
        Serial.println("NAP not planed major issue");
        return;
    }
   // Serial.println("NAP planned" + String(NAPPlaned) + " nap sent " + String(NAPSend) + "NAP current time: " + String(this->currentTime)+" planning " + String(this->myNAP.startTime) + " to " + String(this->myNAP.endTime));
    if (NAPPlaned && !NAPSend && this->myNAP.startTime < this->currentTime &&this->myNAP.endTime> this->currentTime)
    {
        sendNAPPacket();
        Serial.println("Executing NAP transmission");
        return;
    }
}

void DTP::loop()
{
    parseNeigbours();
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
    Serial.println("Packet received");
    DTPPacketUnkown *dtpPacket = (DTPPacketUnkown *)packet->data;

    Serial.println(dtpPacket->type);
    uint16_t dtpSize = size - sizeof(LCMMPacketDataRecieve);
    switch (dtpPacket->type)
    {
    case DTP_PACKET_TYPE_NAP:
        Serial.println("NAP packet received");
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
