#include <DTP.h>
#include "mathextension.h"
DTP *DTP::dtp = nullptr;
DTPPacketNAPRecieve *DTP::neighborPacketToParse = nullptr;
DTPPacketGenericRecieve *DTP::dataPacketToParse = nullptr;
DTPPacketACKRecieve *DTP::ackPacketToParse = nullptr;
bool DTP::ackPacketWaiting = false;
bool DTP::dataPacketWaiting = false;
bool DTP::neighborPacketWaiting = false;
uint16_t DTP::dtpPacketSize = 0;
uint16_t DTP::dataPacketSize = 0;

bool compareByStartTime(const DTPNAPTimeRecord &a, const DTPNAPTimeRecord &b)
{
    return a.startTime < b.startTime;
}

DTP::DTP(uint8_t NAPInterval)
{
    this->state = DTPStates::INITIAL_LISTENING;
    this->NAPInterval = NAPInterval;
    this->neighborPacketWaiting = false;
    this->lastNAPSsentInterval = 0;
    this->numOfIntervalsElapsed = 0;
    this->lastTick = 0;
    this->packetIdCounter = 1;
    this->NAPPlaned = false;

    this->waitingForAck = vector<DTPPacketWaiting>();
    randomSeed((uint64_t)((uint64_t)MAC::getInstance()->random()) << 32 | MAC::getInstance()->random());
    Serial.println("DTP initialized " + String(MAC::getInstance()->random()));

    LCMM::initialize(DTP::receivePacket, DTP::receiveAck);
    MAC::getInstance()->setMode(RECEIVING, true);
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

unordered_map<uint16_t, DTPRoutingItemFull> DTP::getRoutingTable()
{
    return this->routingCacheTable;
}

vector<DTPNAPTimeRecord> DTP::neighbors()
{
    return this->activeNeighbors;
}

DTPNAPTimeRecord DTP::getMyNAP()
{
    return this->myNAP;
}

void DTP::initialize(uint8_t NAPInterval)
{
    printf("Initializing DTP\n");
    if (dtp == nullptr)
        dtp = new DTP(NAPInterval);
}

bool DTP::checkIfCurrentPlanIsColliding()
{
    sort(this->activeNeighbors.begin(), this->activeNeighbors.end(), compareByStartTime);

    for (DTPNAPTimeRecord i : this->activeNeighbors)
    {
        if ((i.startTime < this->myNAP.endTime && i.endTime > this->myNAP.endTime) || (i.startTime < this->myNAP.startTime && i.endTime > this->myNAP.startTime))
            return true;
    }
    return false;
}

void DTP::savePreviousActiveNeighbors()
{
    this->previousActiveNeighbors.clear();
    for (DTPNAPTimeRecord i : this->activeNeighbors)
    {
        DTPNAPTimeRecord newRecord = i;
        newRecord.routes = unordered_map<uint16_t, DTPRoutingItem>(i.routes);
        this->previousActiveNeighbors[i.id] = newRecord;
    }
}

void DTP::cleaningDeamon()
{
    this->NAPSend = false;
    Serial.println("Cleaning deamon");

    // This code removes all neighbors who have not been heard from in the last two intervals. It also removes any routes that go through the removed neighbors.
    routingCacheTable.clear();

    auto neighbourIterator = this->activeNeighbors.begin();
    bool routeIteratorRemoved = false;
    while (neighbourIterator != this->activeNeighbors.end())
    {
        DTPNAPTimeRecord &neighbour = *neighbourIterator;
        printf("Going through %d last interval heard %d num of intervals elapsed %d \n", neighbour.id, neighbour.lastIntervalHeard, numOfIntervalsElapsed);

        if (neighbour.lastIntervalHeard - numOfIntervalsElapsed > 0)
        {
            printf("ereasing %d %d \n", neighbour.lastIntervalHeard, numOfIntervalsElapsed);
            this->activeNeighbors.erase(neighbourIterator);
        }
        else
        {
            printf("going to save the neighbour\n");
            routingCacheTable[neighbour.id] = DTPRoutingItemFull{neighbour.id, 1, true};

            for (auto routing : neighbour.routes)
            {
                printf("going through routes\n");
                DTPRoutingItem &route = routing.second;
                if (routingCacheTable.find(routing.first) == routingCacheTable.end())
                {
                    routingCacheTable[routing.first] = DTPRoutingItemFull{neighbour.id, route.distance};
                }
                else
                {
                    bool sharable = true;
                    if (this->numOfIntervalsElapsed && this->previousActiveNeighbors.find(neighbour.id) != this->previousActiveNeighbors.end() && this->previousActiveNeighbors[neighbour.id].routes.find(routing.first) != this->previousActiveNeighbors[neighbour.id].routes.end() && this->previousActiveNeighbors[neighbour.id].routes[routing.first].distance < route.distance)
                    {
                        printf("unsharable %d source %d", routing.first, neighbour.id);
                        Serial.println("unsharable" + String(routing.first) + " source " + String(neighbour.id));
                        sharable = false;
                    }
                    if (route.distance < routingCacheTable[routing.first].distance && sharable >= routingCacheTable[routing.first].sharable)
                        routingCacheTable[routing.first] = DTPRoutingItemFull{neighbour.id, route.distance, sharable};
                }
            }
            neighbourIterator++;
        }
    }
    printf("sorting now\n");
    sort(this->activeNeighbors.begin(), this->activeNeighbors.end(), compareByStartTime);
    printf("sorted now\n");
    this->savePreviousActiveNeighbors();
    printf("saving\n");

    if (this->numOfIntervalsElapsed)
    {
        printf("num of intervals elapsed check\n");
        ;
        this->myNAP.endTime = this->myNAP.startTime + this->getTimeOnAirOfNAP();
        printf(("is collision eminent?: " + String(this->NAPPlaned)).c_str());
        printf(String(checkIfCurrentPlanIsColliding()).c_str());
        this->NAPPlaned *= !checkIfCurrentPlanIsColliding();
    }
}

uint16_t DTP::getRoutingItem(uint16_t id)
{
    auto iterator = this->routingCacheTable.find(id);
    if (iterator != this->routingCacheTable.end())
    {
        return (*iterator).second.id;
    }
    return 0;
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

void DTP::setPacketRecievedCallback(PacketReceivedCallback fun)
{
    this->recieveCallback = fun;
}

uint16_t DTP::sendPacket(uint8_t *data, uint8_t size, uint16_t target, uint16_t timeout, DTP::PacketAckCallback callback)
{
    uint16_t proxyId = this->getRoutingItem(target);
    if (!proxyId)
        return proxyId;

    DTPPacketGeneric *packet = (DTPPacketGeneric *)malloc(sizeof(DTPPacketGeneric) + size);

    if (!packet)
        return -1;

    packet->finalTarget = target;
    packet->originalSender = MAC::getInstance()->getId();
    packet->id = packetIdCounter++;
    packet->type = DTP_PACKET_TYPE_DATA_SINGLE;
    memcpy(packet->data, data, size);

    LCMM::getInstance()->sendPacketSingle(true, proxyId, (uint8_t *)packet, sizeof(DTPPacketGeneric) + size, DTP::receiveAck);
    DTPPacketWaiting waitingPacket;
    waitingPacket.id = packet->id;
    waitingPacket.timeout = timeout;
    waitingPacket.timeLeft = timeout;
    this->waitingForAck.push_back(waitingPacket);
    return packet->id;
}

void DTP::parseDataPacket()
{
    if (!dataPacketWaiting)
        return;

    Serial.println("Parsing data");

    dataPacketWaiting = false;

    DTPPacketGenericRecieve *packet = dataPacketToParse;

    if (packet->finalTarget == MAC::getInstance()->getId())
    {
        DTPPacketACK *responsePacket = (DTPPacketACK *)malloc(sizeof(DTPPacketACK));
        responsePacket->header.finalTarget = packet->originalSender;
        responsePacket->header.id = this->packetIdCounter++;
        responsePacket->header.originalSender = MAC::getInstance()->getId();
        responsePacket->header.type = DTP_PACKET_TYPE_ACK;
        responsePacket->responseId = packet->id;

        Serial.println("sending ack " + String(packet->id)+ " to " + String(packet->originalSender) + " throught " + String(packet->lcmm.mac.sender) + " with id " + String(responsePacket->header.id));
        LCMM::getInstance()->sendPacketSingle(false, packet->lcmm.mac.sender, (unsigned char *)responsePacket, sizeof(DTPPacketACK), DTP::receiveAck);
        this->recieveCallback(packet, this->dataPacketSize);
        this->dataPacketToParse = nullptr;
        this->dataPacketSize = 0;
    }
    else
    {
        uint16_t proxyId = this->getRoutingItem(packet->finalTarget);
        if (proxyId != 0)
        {
            Serial.println("routing found");
            DTPPacketGeneric *proxyPacket = (DTPPacketGeneric *)malloc(this->dataPacketSize);
            if (!proxyPacket)
            {
                Serial.println("memory allloc failed major fuckup");
                return;
            }

            memcpy(proxyPacket, ((LCMMPacketDataRecieve *)packet)->data, this->dataPacketSize);

            LCMM::getInstance()->sendPacketSingle(true, proxyId, (unsigned char *)proxyPacket, this->dataPacketSize, DTP::receiveAck);
        }
        else
        {

            Serial.println("havent found target");
            // possible implementation with flood routing
        }

        if (this->dataPacketToParse)
        {
            free(this->dataPacketToParse);
            this->dataPacketToParse = nullptr;
        }
    }
}

void DTP::parseNeigbours()
{
    if (!neighborPacketWaiting)
    {
        return;
    }

    printf("Parsing neighbours\n");

    neighborPacketWaiting = false;

    DTPPacketNAPRecieve *packet = neighborPacketToParse;
    uint16_t size = dtpPacketSize;
    uint16_t numOfNeighbors = (size - sizeof(DTPPacketNAP)) / sizeof(NeighborRecord);

    NeighborRecord *neighbors = packet->neighbors;
    Serial.println("Parsing neighbours " + String(numOfNeighbors));

    float timeOnAir = MathExtension.timeOnAir(size, 8, 9, 125, 7);
    uint32_t startTime = currentTime - timeOnAir * 1.05;
    uint32_t endTime = currentTime;

    uint16_t senderId = packet->lcmm.mac.sender;

    unordered_map<uint16_t, DTPRoutingItem> routes;

    for (int i = 0; i < numOfNeighbors; i++)
    {
        if (MAC::getInstance()->getId() != neighbors[i].id)
            routes[neighbors[i].id] = (DTPRoutingItem){neighbors[i].distance};
    }

    bool alreadyInNeighbours = false;
    for (unsigned int i = 0; i < this->activeNeighbors.size(); i++)
    {
        Serial.println("Going thtough neihbours");

        if (this->activeNeighbors[i].id == senderId)
        {
            this->activeNeighbors[i].startTime = startTime;
            this->activeNeighbors[i].endTime = endTime;
            this->activeNeighbors[i].lastIntervalHeard = this->numOfIntervalsElapsed;
            // fairy tale dust for maybe clearing heap
            this->activeNeighbors[i].routes.reserve(0);
            this->activeNeighbors[i].routes = routes;
            alreadyInNeighbours = true;
            break;
        }
    }
    if (!alreadyInNeighbours)
    {
        Serial.println("pushing into neighbours");
        this->activeNeighbors.push_back((DTPNAPTimeRecord){senderId, startTime, endTime, this->numOfIntervalsElapsed, routes});
    }

    if (this->neighborPacketToParse)
    {
        free(this->neighborPacketToParse);
        this->neighborPacketToParse = nullptr;
    }
}

size_t DTP::sizeOfRouting()
{
    size_t size = 0;
    vector<uint16_t> alreadyAdded;
    for (auto activeNeighbour : this->activeNeighbors)
    {
        for (auto route : activeNeighbour.routes)
        {
            if (find(alreadyAdded.begin(), alreadyAdded.end(), route.first) == alreadyAdded.end())
            {
                size += sizeof(NeighborRecord);
                alreadyAdded.push_back(route.first);
            }
        }
    }

    return size;
}

uint32_t DTP::getTimeOnAirOfNAP()
{
    size_t sendableItems = 0;
    for (auto element : this->routingCacheTable)
    {
        if (element.second.sharable)
            sendableItems++;
    }
    uint16_t sizeOfRouting = sendableItems * sizeof(NeighborRecord) + sizeof(DTPPacketNAP) + LCMM_OVERHEAD;
    return MathExtension.timeOnAir(sizeOfRouting, 8, 9, 125.0, 7);
}

void DTP::NAPPlanRandom()
{
    printf("planning ranodm\n");

    uint32_t timeOnAir = getTimeOnAirOfNAP();
    uint32_t minTime = currentTime + timeOnAir;
    uint32_t maxTime = this->NAPInterval * 1000 - timeOnAir * 1.3;
    uint32_t chosenTime = MathExtension.getRandomNumber(minTime, maxTime);
    this->myNAP.startTime = chosenTime;
    this->myNAP.endTime = chosenTime + timeOnAir;
    printf(("NAP planned for " + String(chosenTime) + " to " + String(chosenTime + timeOnAir)).c_str());
    this->NAPPlaned = true;
}

DTPNAPTimeRecordSimple DTP::getNearestTimeSlot(uint32_t ideal_min_time, uint32_t ideal_max_time, uint32_t min_start_time, uint32_t max_end_time)
{

    vector<DTPNAPTimeRecordSimple> possibleTimeSlots;

    auto iterator = this->activeNeighbors.begin();

    while (iterator != this->activeNeighbors.end())
    {
        DTPNAPTimeRecordSimple neighbour = {(*iterator).startTime, (*iterator).endTime};
        DTPNAPTimeRecordSimple nextNeighbour = {max_end_time, max_end_time};

        if ((iterator + 1) != this->activeNeighbors.end())
            nextNeighbour = {(*(iterator + 1)).startTime, (*(iterator + 1)).endTime};

        if (nextNeighbour.startTime - neighbour.endTime > ideal_max_time - ideal_min_time)
        {
            possibleTimeSlots.push_back(DTPNAPTimeRecordSimple{neighbour.endTime, nextNeighbour.startTime});
        }
        iterator++;
    }

    if (possibleTimeSlots.size() == 0)
        return DTPNAPTimeRecordSimple{0, 0};

    DTPNAPTimeRecordSimple bestTimeSlot = possibleTimeSlots[0];

    for (DTPNAPTimeRecordSimple i : possibleTimeSlots)
    {
        if (i.endTime - i.startTime < bestTimeSlot.endTime - bestTimeSlot.startTime)
            bestTimeSlot = i;
    }

    int howMuchCanFit = (bestTimeSlot.endTime - bestTimeSlot.startTime) / (ideal_max_time - ideal_min_time);
    Serial.println("how much can fit " + String(howMuchCanFit));
    if (howMuchCanFit > 1)
    {
        int margin = (bestTimeSlot.endTime - bestTimeSlot.startTime) - howMuchCanFit * (ideal_max_time - ideal_min_time);
        Serial.println("margin " + String(margin));
        int sideMargin = margin / (howMuchCanFit * 2);
        int timeSlotChosen = MathExtension.getRandomNumber(0, howMuchCanFit - 1);
        Serial.println("time slot chosen " + String(timeSlotChosen));
        bestTimeSlot.startTime = bestTimeSlot.startTime + timeSlotChosen * (sideMargin + ideal_max_time - ideal_min_time);
        bestTimeSlot.endTime = bestTimeSlot.startTime + (ideal_max_time - ideal_min_time);
        Serial.println("best time slot " + String(bestTimeSlot.startTime) + " to " + String(bestTimeSlot.endTime));
    }

    return bestTimeSlot;
}

void DTP::NAPPlanInteligent()
{
    uint32_t timeOnAir = getTimeOnAirOfNAP();

    uint32_t denominator = 0;
    uint32_t numerator = 0;
    sort(this->activeNeighbors.begin(), this->activeNeighbors.end(), compareByStartTime);

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

    DTPNAPTimeRecordSimple bestTimeSlot = getNearestTimeSlot(idealMinTime, idealMaxTime, 0, this->NAPInterval * 1000);

    if (bestTimeSlot.startTime == 0 && bestTimeSlot.endTime == 0)
    {
        this->NAPPlanRandom();
        return;
    }
    printf(("planning intelligent\n" + String(bestTimeSlot.startTime) + " to " + String(bestTimeSlot.endTime)).c_str());
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
    vector<NeighborRecord> sendableItems;
    for (auto element : this->routingCacheTable)
    {
        if (element.second.sharable)
        {
            printf("sending %d %d\n", element.first, element.second.distance);
            Serial.println("sending " + String(element.first) + " " + String(element.second.distance));
            sendableItems.push_back(NeighborRecord{element.first, element.second.distance});
        }
    }
    uint16_t sizeOfRouting = sendableItems.size() * sizeof(NeighborRecord) + sizeof(DTPPacketNAP);

    DTPPacketNAP *packet = (DTPPacketNAP *)malloc(sizeOfRouting);

    packet->type = DTP_PACKET_TYPE_NAP;
    int i = 0;
    for (NeighborRecord item : sendableItems)
    {
        packet->neighbors[i].id = item.id;
        packet->neighbors[i].distance = item.distance + 1;
        i++;
    }

    for (int i = 0; i < sendableItems.size(); i++)
    {
        printf("sending %d %d\n", packet->neighbors[i].id, packet->neighbors[i].distance);
        Serial.println("sending " + String(packet->neighbors[i].id) + " " + String(packet->neighbors[i].distance));
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
        printf("No neighbours\n");
        // printf("size of active neighbours %d", this->activeNeighbors.size());
        Serial.println("No neighbours");
        NAPPlanRandom();
    }

    if (!NAPPlaned)
    {
        Serial.println("Planning intelligent");
        printf("Planning inteligently");

        this->NAPPlanInteligent();
    }
    if (!NAPPlaned)
    {
        printf("NAP not planed major issue\n");
        return;
    }
    // Serial.println("NAP planned" + String(NAPPlaned) + " nap sent " + String(NAPSend) + "NAP current time: " + String(this->currentTime)+" planning " + String(this->myNAP.startTime) + " to " + String(this->myNAP.endTime));
    if (NAPPlaned && !NAPSend && this->myNAP.startTime<this->currentTime &&this->myNAP.endTime> this->currentTime)
    {
        sendNAPPacket();
        printf("Executing NAP transmission\n");
        return;
    }
}

void DTP::timeoutDeamon()
{
    auto it = this->waitingForAck.begin();
    uint32_t currTime = millis();
    while (it != this->waitingForAck.end())
    {
        // If element is even number then delete it
        DTPPacketWaiting waitingPacket = *it;
        if (currTime - this->lastTick >= waitingPacket.timeLeft)
        {
            // Due to deletion in loop, iterator became
            // invalidated. So reset the iterator to next item.
            waitingPacket.callback(0);

            it = this->waitingForAck.erase(it);
        }
        else
        {
            waitingPacket.timeLeft -= currTime - this->lastTick;
            it++;
        }
    }

    this->lastTick = millis();
}

void DTP::parseAckPacket()
{
    if (ackPacketWaiting)
    {
        ackPacketWaiting = false;
        DTPPacketACKRecieve *packet = ackPacketToParse;
        uint16_t id = packet->responseId;
        auto it = this->waitingForAck.begin();
        while (it != this->waitingForAck.end())
        {
            // If element is even number then delete it
            DTPPacketWaiting waitingPacket = *it;
            if (waitingPacket.id == id)
            {
                // Due to deletion in loop, iterator became
                // invalidated. So reset the iterator to next item.
                waitingPacket.callback(1);
                it = this->waitingForAck.erase(it);
            }
            else
            {
                it++;
            }
        }
        if (this->ackPacketToParse)
            free(this->ackPacketToParse);
        this->ackPacketToParse = nullptr;
    }
}

void DTP::loop()
{
    parseNeigbours();
    LCMM::getInstance()->loop();
    updateTime();
    sendNAP();
    parseDataPacket();
    parseAckPacket();
    timeoutDeamon();

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
    printf("Packet received %d\n", dtpPacket->type);
    switch (dtpPacket->type)
    {
    case DTP_PACKET_TYPE_NAP:
        Serial.println("NAP packet received");
        DTP::neighborPacketWaiting = true;
        DTP::neighborPacketToParse = (DTPPacketNAPRecieve *)packet;
        DTP::dtpPacketSize = dtpSize;
        break;
    case DTP_PACKET_TYPE_DATA_SINGLE:
        Serial.println("DATA packet received");
        DTP::dataPacketWaiting = true;
        DTP::dataPacketToParse = (DTPPacketGenericRecieve *)packet;
        DTP::dataPacketSize = dtpSize;
        break;
    case DTP_PACKET_TYPE_ACK:
        Serial.println("ACK packet received");
        printf("ack recieved");
        DTP::ackPacketWaiting = true;
        DTP::ackPacketToParse = (DTPPacketACKRecieve *)packet;
        break;
    }
}

void DTP::receiveAck(uint16_t id, bool success)
{
    printf("Ack received\n");
}
