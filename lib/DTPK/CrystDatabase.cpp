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
        if (std::find(this->crystalizationSessionIds.begin(), this->crystalizationSessionIds.end(), record->first) == this->crystalizationSessionIds.end())
        {
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
        for (auto subRecord = this->routeToId.begin(); subRecord != this->routeToId.end(); subRecord++)
        {

            auto alreadyInCache = this->idToRouteCache.find(subRecord->second.id);

            if (alreadyInCache != this->idToRouteCache.end())
            {
                if (alreadyInCache->second.distance <= subRecord->second.distance)
                    continue;
            }

            this->idToRouteCache[subRecord->second.id] = RoutingRecord{record->first, record->second.from, subRecord->second.distance};
        }
    }
}

// possible to change mulimap to unordered_map inside unordered_map so it would not be O(n^2)
bool CrystDatabase::updateFromCrystPacket(uint16_t from, NeighborRecord *neighbours, int numOfNeighbours)
{
    bool change = false;
    auto range = this->routeToId.equal_range(from);
    int numOfCurrentNeighbours = std::distance(range.first, range.second) - 1;
    unordered_multimap<uint16_t, NeighborRecord>::iterator record = this->routeToId.find(from);
    crystalizationSessionIds.push_back(from);
  
    if (numOfNeighbours == 0 || numOfCurrentNeighbours != numOfNeighbours || record == this->routeToId.end())
    {
        printf("not the same number of neighbours %d %d\n", numOfNeighbours, numOfCurrentNeighbours);
        change = true;
    }
    else
    {
        
            for (int i = 0; i < numOfNeighbours && !change; i++)
            {
                bool found = false;
                for (auto it = range.first; it != range.second && !change; ++it)
                {
                    if (neighbours[i].id == it->second.id)
                    {
                        printf("neighbour found\n");
                        if (neighbours[i].from != it->second.from || neighbours[i].distance != it->second.distance)
                        {
                            printf("neighbour not the same\n");
                            printf("from %d %d\n", neighbours[i].from, it->second.from);
                            change = true;
                            break;
                        }
                        found = true;
                        break;
                    }
                }
                if (!found)
                    change = true;
            }
        
    }

    if (!change)
    {
        return false;
    }
    else
    {
        this->routeToId.erase(from);
    }

    NeighborRecord senderRecord;
    senderRecord.id = from;
    senderRecord.from = this->myId;
    senderRecord.distance = 1;
    this->routeToId.emplace(from, senderRecord);

    for (int i = 0; i < numOfNeighbours; i++)
    {
        if (neighbours[i].id == this->myId || neighbours[i].from == this->myId)
        {
            continue;
        }

        change = true;

        NeighborRecord neighbour = neighbours[i];
        neighbour.distance++;
        routeToId.emplace(from, neighbour);
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
        neighbour.from = record->second.originalRouter;
        neighbour.distance = record->second.distance;
        neighbors.push_back(neighbour);
    }

    return neighbors; // Replace with actual logic
}