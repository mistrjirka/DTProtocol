#include <CrystDatabase.h>

CrystDatabase::CrystDatabase()
{
    // Constructor implementation
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
    return false; // Replace with actual logic
}

bool CrystDatabase::addRouting(uint16_t id, NeighborRecord record)
{
    this->routeToId.emplace(id, record);
    this->buildCache();
    return false; // Replace with actual logic
}

void CrystDatabase::changeMap(unordered_multimap<uint16_t, NeighborRecord> newmap)
{
    this->routeToId = newmap;
    this->buildCache();
}

unordered_multimap<uint16_t, NeighborRecord> CrystDatabase::getMap()
{
    // Implementation of getMap method
    return this->routeToId; // Replace with actual logic
}

void CrystDatabase::buildCache()
{
    this->idToRouteCache.clear();

    for (auto record = this->routeToId.begin(); record != this->routeToId.end(); record++)
    {
        RoutingRecord provider;
        provider.router = record->first;
        provider.distance = 1;

        this->idToRouteCache[record->first] = provider;

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
    int numOfCurrentNeighbours = std::distance(range.first, range.second);
    unordered_multimap<uint16_t, NeighborRecord>::iterator record = this->routeToId.find(from);

    if (numOfNeighbours != numOfCurrentNeighbours)
    {
        change = true;
    }
    else if (record == this->routeToId.end())
    {
        change = true;
    }
    else
    {
        for (int i = 0; i < numOfNeighbours && !change; i++)
        {
            for (auto it = range.first; it != range.second && !change; ++it)
            {
                if (neighbours[i].id == it->second.id && neighbours[i].distance == it->second.distance)
                {
                    change = true;
                    break;
                }
            }
        }
    }

    if(!change){
        return false;
    }

    for (int i = 0; i < numOfNeighbours; i++)
    {
        if (change == true)
        {
            this->routeToId.erase(from);
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