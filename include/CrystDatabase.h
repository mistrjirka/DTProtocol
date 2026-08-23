#ifndef CRYST_DATABASE_H
#define CRYST_DATABASE_H

#include <stdint.h>
#include <stddef.h>
#include <algorithm>
#include <unordered_map>
#include <vector>

#include <DTPKDefinitions.h>

struct CrystSequenceRequest
{
    uint16_t destination;
    uint16_t requestedSequence;
};

class CrystDatabase
{
public:
    explicit CrystDatabase(uint16_t id);

    RoutingRecord *getRouting(uint16_t id);
    const RoutingRecord *getRouting(uint16_t id) const;
    bool hasKnownDestination(uint16_t id) const;
    bool hasNewerKnownSequence(uint16_t destination,
                               uint16_t sequence) const;
    bool getRepairNextHop(uint16_t destination,
                          uint16_t avoidedRouter,
                          uint16_t &result) const;
    bool getFeasibleAlternateRoute(
        uint16_t destination,
        uint16_t avoidedRouter,
        RoutingRecord &result) const;

    // A HELLO proves the direct neighbour alive. If `invalidateIndirect` is
    // true (new incarnation / no full-state knowledge), all old indirect
    // contribution from that neighbour is discarded immediately.
    bool updateDirectNeighbor(uint16_t from,
                              uint16_t originSequence,
                              bool invalidateIndirect = false);

    // Replace one neighbour's complete contribution transactionally after the
    // DTPK layer has reassembled all CRYST chunks.
    bool updateFromCrystPacket(uint16_t from,
                               uint16_t originSequence,
                               const NeighborRecordV2 *records,
                               size_t count);

    bool removeNeighbor(uint16_t from);

    // Feasible distance is based on state this node actually advertises. Call
    // immediately before serialising a route-state snapshot.
    void noteAdvertisedRoutes();

    std::vector<NeighborRecordV2> getListOfRoutesV2() const;

    // Backward-compatible application/UI view used by Picopod.
    std::vector<NeighborRecord> getListOfNeighbours() const;

    // Rebuilds that lose a destination solely because every candidate is
    // infeasible enqueue a generation request. DTPK applies flood cooldown and
    // transmission policy.
    std::vector<CrystSequenceRequest> takeSequenceRequests();

private:
    struct Candidate
    {
        uint16_t destination;
        uint16_t router;
        uint16_t sequence;
        uint8_t distance;
        uint8_t neighborMetric;
    };

    struct FeasibilityRecord
    {
        uint16_t sequence;
        uint8_t feasibleDistance;
    };

    uint16_t myId;
    std::unordered_map<uint16_t, std::vector<Candidate>> routesByNeighbor;
    std::unordered_map<uint16_t, RoutingRecord> routeCache;
    std::unordered_map<uint16_t, FeasibilityRecord> feasibility;
    std::vector<CrystSequenceRequest> pendingSequenceRequests;

    static bool sequenceNewer(uint16_t a, uint16_t b);
    static uint16_t nextSequence(uint16_t value);
    static bool candidateEqual(const Candidate &a, const Candidate &b);
    static bool routeEqual(const RoutingRecord &a, const RoutingRecord &b);

    bool candidateFeasible(const Candidate &candidate) const;
    bool rebuildCache();
    void queueSequenceRequest(uint16_t destination, uint16_t requestedSequence);
    static void sortCandidates(std::vector<Candidate> &records);
};

#endif
