#include <CrystDatabase.h>

CrystDatabase::CrystDatabase(uint16_t id)
{
    this->myId = id;
    this->crystalizationSession = false;
}

RoutingRecord *CrystDatabase::getRouting(uint16_t id)
{

    auto record = this->idToRouteCache.find(id);

    if (record != this->idToRouteCache.end())
        return &record->second;

    return nullptr; // Replace with actual logic
}

bool CrystDatabase::removeRouting(uint16_t id)
{
    this->idToRouteCache.erase(id);
    return true;
}

bool CrystDatabase::addRouting(uint16_t id, NeighborRecord record)
{
    this->routeToId.emplace(id, record);
    this->buildCache();
    return true;
}

bool CrystDatabase::addRoutingFromDataPacket(uint16_t from, uint16_t originalSender)
{
    /*
    auto existingRecord = this->routeToId.find(from);
    if(existingRecord != this->routeToId.end())
    {

        for(auto it = (*existingRecord).second->)
    }
    */
    return false;
}

void CrystDatabase::changeMap(unordered_multimap<uint16_t, NeighborRecord> newmap)
{
    this->routeToId = newmap;
    this->buildCache();
}

unordered_multimap<uint16_t, NeighborRecord> CrystDatabase::getMap()
{
    // Implementation of getMap method
    return this->routeToId;
}

bool CrystDatabase::isInCrystalizationSession()
{
    return this->crystalizationSession;
}

void CrystDatabase::startCrystalizationSession()
{
    this->crystalizationSessionIds.clear();
    this->crystalizationSession = true;
}

bool CrystDatabase::endCrystalizationSession()
{
    bool change = false;
    for (auto record = this->routeToId.begin(); record != this->routeToId.end(); record++)
    {
        printf("checking record %d\n", record->first);
        if (std::find(this->crystalizationSessionIds.begin(), this->crystalizationSessionIds.end(), record->first) == this->crystalizationSessionIds.end())
        {
            printf("erasing record %d\n", record->first);
            printf("deleteing from crystalization session\n");
            change = true;
            this->routeToId.erase(record);
        }
    }
    this->crystalizationSession = false;
    this->crystalizationSessionIds.clear();
    buildCache();
    return change;
}

void CrystDatabase::buildCache()
{
    this->idToRouteCache.clear();

    for (auto record = this->routeToId.begin(); record != this->routeToId.end(); record++)
    {

        auto alreadyInCache = this->idToRouteCache.find(record->second.id);

        if (alreadyInCache != this->idToRouteCache.end())
        {
            if (alreadyInCache->second.distance <= record->second.distance)
                continue;
        }

        Serial.println("adding to cache: " + String(record->first) + " to: " + String(record->second.id) + " distance: " + String(record->second.distance));

        this->idToRouteCache[record->second.id] = RoutingRecord{record->first, record->second.from, record->second.distance};
    }
}

vector<NeighborRecord> CrystDatabase::getNeighboursFromPacket(uint16_t from, NeighborRecord *packet, int numOfNeighbours)
{
    vector<NeighborRecord> neighbours;
    NeighborRecord senderRecord;
    senderRecord.id = from;
    senderRecord.from = this->myId;
    senderRecord.distance = 1;

    neighbours.push_back(senderRecord);

    for (int i = 0; i < numOfNeighbours; i++)
    {
        if (packet[i].id == this->myId || packet[i].from == this->myId)
        {
            continue;
        }
        Serial.println("previous distance: " + String(packet[i].distance) + " new distance: " + String(packet[i].distance) + " packet distance + 1" + String(packet[i].distance + 1));
        NeighborRecord record;
        record.id = packet[i].id;
        record.from = from;
        record.distance = packet[i].distance + 1;
        Serial.println("adding neighbour to list: " + String(record.id) + " from: " + String(record.from) + " distance: " + String(record.distance));
        neighbours.push_back(record);
    }
    return neighbours;
}

bool CrystDatabase::compareNeighbours(std::vector<NeighborRecord> a, std::vector<NeighborRecord> b)
{
    if (a.size() != b.size())
    {
        return false;
    }

    std::sort(a.begin(), a.end(), [](const NeighborRecord &lhs, const NeighborRecord &rhs)
              { return lhs.id < rhs.id; });
    std::sort(b.begin(), b.end(), [](const NeighborRecord &lhs, const NeighborRecord &rhs)
              { return lhs.id < rhs.id; });

    for (size_t i = 0; i < a.size(); i++)
    {
        if (a[i].id != b[i].id || a[i].from != b[i].from || a[i].distance != b[i].distance)
        {
            return false;
        }
    }

    return true;
}

bool CrystDatabase::updateFromCrystPacket(uint16_t from, NeighborRecord *neighbours, int numOfNeighbours)
{
    auto range = this->routeToId.equal_range(from);
    unordered_multimap<uint16_t, NeighborRecord>::iterator record = this->routeToId.find(from);
    crystalizationSessionIds.push_back(from);

    vector<NeighborRecord> incomingNeighbours = getNeighboursFromPacket(from, neighbours, numOfNeighbours);
    std::vector<NeighborRecord> originalNeighbours;
    for (auto it = range.first; it != range.second; ++it)
    {
        originalNeighbours.push_back(it->second);
    }

    if (compareNeighbours(incomingNeighbours, originalNeighbours))
    {
        printf("nothing changed\n");
        return false;
    }

    printf("something changed\n");

    this->routeToId.erase(from);

    for (auto incomingNeighboursIterator = incomingNeighbours.begin(); incomingNeighboursIterator != incomingNeighbours.end(); incomingNeighboursIterator++)
    {
        Serial.println(" adding neighbour to routeToId router:" + String(from) + " to: " + String(incomingNeighboursIterator->id));
        this->routeToId.emplace(from, *incomingNeighboursIterator);
    }

    this->buildCache();

    return true;
}

std::vector<NeighborRecord> CrystDatabase::getListOfNeighbours()
{
    // Implementation of getListOfNeighbours method
    std::vector<NeighborRecord> neighbors;

    for (auto record = this->idToRouteCache.begin(); record != this->idToRouteCache.end(); record++)
    {
        NeighborRecord neighbour;
        neighbour.id = record->first;
        neighbour.from = record->second.router;
        neighbour.distance = record->second.distance;
        neighbors.push_back(neighbour);
    }

    return neighbors; // Replace with actual logic
}