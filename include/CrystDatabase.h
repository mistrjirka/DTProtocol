#ifndef CRYST_DATABASE_H
#define CRYST_DATABASE_H

#include <DPTKDefinitions.h>
#include <unordered_map>


class CrystDatabase
{
    public:

    NeighborRecord *getRouting(uint16_t id);

    bool removeRouting(uint16_t id);

    bool updateRouting(uint16_t id, NeighborRecord);

    void changeMap(unordered_map<uint16_t, NeighborRecord>);

    unordered_map<uint16_t, NeighborRecord> getMap();

    void updateFromCrystPacket(uint16_t from, NeighborRecord* packet, uint16_t size);

    vector<NeighborRecord> getListOfNeighbours();

    CrystDatabase();
    
    private:
    
    unordered_map<uint16_t, NeighborRecord> neighborMap;

};

#endif
