#ifndef CRYST_DATABASE_H
#define CRYST_DATABASE_H

#include <DPTKDefinitions.h>
#include <unordered_map>

class CrystDatabase
{
public:
    RoutingRecord *getRouting(uint16_t id);

    bool removeRouting(uint16_t id);

    bool addRouting(uint16_t id, NeighborRecord);

    void changeMap(unordered_multimap<uint16_t, NeighborRecord>);

    unordered_multimap<uint16_t, NeighborRecord> getMap();

    void updateFromCrystPacket(uint16_t from, NeighborRecord *packet, int numOfNeighbours);

    void buildCache();

    vector<NeighborRecord> getListOfNeighbours();

    CrystDatabase();

private:
    unordered_multimap<uint16_t, NeighborRecord> routeToId;
    unordered_map<uint16_t, RoutingRecord> idToRouteCache;
};

#endif
