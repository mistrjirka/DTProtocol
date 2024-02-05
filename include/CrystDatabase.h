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

    bool updateFromCrystPacket(uint16_t from, NeighborRecord *packet, int numOfNeighbours);

    void buildCache();

    bool isInCrystalizationSession();

    void startCrystalizationSession();
    
    bool endCrystalizationSession();

    vector<NeighborRecord> getListOfNeighbours();

    CrystDatabase(uint16_t id);

private:
    uint16_t myId;
    bool crystalizationSession;
    vector<uint16_t> crystalizationSessionIds; // ids of nodes that are in crystalization session. In the end everything that is not in the crystalization session will be deleted
    unordered_multimap<uint16_t, NeighborRecord> routeToId;
    unordered_map<uint16_t, RoutingRecord> idToRouteCache;
};

#endif
