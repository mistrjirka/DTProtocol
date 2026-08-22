#include <CrystDatabase.h>

#include <limits>

CrystDatabase::CrystDatabase(uint16_t id) : myId(id) {}

RoutingRecord *CrystDatabase::getRouting(uint16_t id)
{
    auto it = routeCache.find(id);
    return it == routeCache.end() ? nullptr : &it->second;
}

bool CrystDatabase::hasKnownDestination(uint16_t id) const
{
    for (const auto &neighborEntry : routesByNeighbor)
    {
        for (const Candidate &candidate : neighborEntry.second)
        {
            if (candidate.destination == id)
                return true;
        }
    }
    return false;
}

bool CrystDatabase::sequenceNewer(uint16_t a, uint16_t b)
{
    if (a == b)
        return false;
    return static_cast<uint16_t>(a - b) < 0x8000u;
}

uint16_t CrystDatabase::nextSequence(uint16_t value)
{
    value = static_cast<uint16_t>(value + 1u);
    return value == 0 ? 1 : value;
}

bool CrystDatabase::candidateEqual(const Candidate &a, const Candidate &b)
{
    return a.destination == b.destination &&
           a.router == b.router &&
           a.sequence == b.sequence &&
           a.distance == b.distance &&
           a.neighborMetric == b.neighborMetric;
}

bool CrystDatabase::routeEqual(const RoutingRecord &a, const RoutingRecord &b)
{
    return a.router == b.router &&
           a.distance == b.distance &&
           a.sequence == b.sequence;
}

void CrystDatabase::sortCandidates(std::vector<Candidate> &records)
{
    std::sort(records.begin(), records.end(), [](const Candidate &a, const Candidate &b) {
        if (a.destination != b.destination)
            return a.destination < b.destination;
        if (a.sequence != b.sequence)
            return a.sequence < b.sequence;
        if (a.distance != b.distance)
            return a.distance < b.distance;
        return a.router < b.router;
    });
}

bool CrystDatabase::candidateFeasible(const Candidate &candidate) const
{
    if (candidate.distance >= DTPK_ROUTE_INFINITY)
        return false;

    auto stateIt = feasibility.find(candidate.destination);
    if (stateIt == feasibility.end())
        return true;

    const FeasibilityRecord &state = stateIt->second;
    if (sequenceNewer(candidate.sequence, state.sequence))
        return true;
    if (candidate.sequence != state.sequence)
        return false;

    // The feasibility test is against the metric reported by our neighbour,
    // not our local +1 metric.
    return candidate.neighborMetric < state.feasibleDistance;
}

void CrystDatabase::queueSequenceRequest(uint16_t destination, uint16_t requestedSequence)
{
    for (auto &request : pendingSequenceRequests)
    {
        if (request.destination != destination)
            continue;
        if (sequenceNewer(requestedSequence, request.requestedSequence))
            request.requestedSequence = requestedSequence;
        return;
    }
    pendingSequenceRequests.push_back({destination, requestedSequence});
}

bool CrystDatabase::rebuildCache()
{
    std::unordered_map<uint16_t, RoutingRecord> nextCache;
    std::unordered_map<uint16_t, bool> blocked;

    for (const auto &neighborEntry : routesByNeighbor)
    {
        for (const Candidate &candidate : neighborEntry.second)
        {
            if (candidate.destination == myId || candidate.distance >= DTPK_ROUTE_INFINITY)
                continue;

            if (!candidateFeasible(candidate))
            {
                blocked[candidate.destination] = true;
                continue;
            }

            RoutingRecord candidateRoute{
                candidate.router,
                candidate.distance,
                candidate.sequence};

            auto existing = nextCache.find(candidate.destination);
            const bool newerGeneration =
                existing != nextCache.end() &&
                sequenceNewer(candidateRoute.sequence, existing->second.sequence);
            const bool sameGenerationBetterMetric =
                existing != nextCache.end() &&
                candidateRoute.sequence == existing->second.sequence &&
                (candidateRoute.distance < existing->second.distance ||
                 (candidateRoute.distance == existing->second.distance &&
                  candidateRoute.router < existing->second.router));
            if (existing == nextCache.end() ||
                newerGeneration ||
                sameGenerationBetterMetric)
            {
                nextCache[candidate.destination] = candidateRoute;
            }
        }
    }

    // If feasibility is the only reason a known destination disappeared, ask
    // its origin for a newer generation. DTPK handles flooding/cooldown.
    for (const auto &entry : blocked)
    {
        const uint16_t destination = entry.first;
        if (nextCache.find(destination) != nextCache.end())
            continue;
        auto state = feasibility.find(destination);
        if (state != feasibility.end())
            queueSequenceRequest(destination, nextSequence(state->second.sequence));
    }

    bool changed = nextCache.size() != routeCache.size();
    if (!changed)
    {
        for (const auto &entry : nextCache)
        {
            auto old = routeCache.find(entry.first);
            if (old == routeCache.end() || !routeEqual(old->second, entry.second))
            {
                changed = true;
                break;
            }
        }
    }

    routeCache.swap(nextCache);
    return changed;
}

bool CrystDatabase::updateDirectNeighbor(uint16_t from,
                                         uint16_t originSequence,
                                         bool invalidateIndirect)
{
    std::vector<Candidate> &records = routesByNeighbor[from];
    if (invalidateIndirect)
        records.clear();

    Candidate direct{from, from, originSequence, 1, 0};
    bool found = false;
    bool contributionChanged = invalidateIndirect;
    for (Candidate &candidate : records)
    {
        if (candidate.destination != from)
            continue;
        found = true;
        if (!candidateEqual(candidate, direct))
        {
            candidate = direct;
            contributionChanged = true;
        }
        break;
    }
    if (!found)
    {
        records.push_back(direct);
        contributionChanged = true;
    }

    if (!contributionChanged)
        return false;
    sortCandidates(records);
    return rebuildCache();
}

bool CrystDatabase::updateFromCrystPacket(uint16_t from,
                                          uint16_t originSequence,
                                          const NeighborRecordV2 *records,
                                          size_t count)
{
    std::vector<Candidate> incoming;
    incoming.reserve(count + 1);
    incoming.push_back({from, from, originSequence, 1, 0});

    for (size_t i = 0; i < count; ++i)
    {
        const NeighborRecordV2 &record = records[i];
        if (record.id == myId || record.id == from || record.from == myId)
            continue;
        if (record.distance >= DTPK_ROUTE_INFINITY - 1)
            continue;

        const uint8_t localDistance = static_cast<uint8_t>(record.distance + 1u);
        if (localDistance >= DTPK_ROUTE_INFINITY)
            continue;

        incoming.push_back({
            record.id,
            from,
            record.sequence,
            localDistance,
            record.distance});
    }

    sortCandidates(incoming);
    std::vector<Candidate> old = routesByNeighbor[from];
    sortCandidates(old);

    bool contributionChanged = incoming.size() != old.size();
    if (!contributionChanged)
    {
        for (size_t i = 0; i < incoming.size(); ++i)
        {
            if (!candidateEqual(incoming[i], old[i]))
            {
                contributionChanged = true;
                break;
            }
        }
    }

    if (!contributionChanged)
        return false;

    routesByNeighbor[from] = std::move(incoming);
    return rebuildCache();
}

bool CrystDatabase::removeNeighbor(uint16_t from)
{
    auto it = routesByNeighbor.find(from);
    if (it == routesByNeighbor.end())
        return false;
    routesByNeighbor.erase(it);
    return rebuildCache();
}

void CrystDatabase::noteAdvertisedRoutes()
{
    for (const auto &entry : routeCache)
    {
        const uint16_t destination = entry.first;
        const RoutingRecord &route = entry.second;
        if (route.distance >= DTPK_ROUTE_INFINITY)
            continue;

        auto state = feasibility.find(destination);
        if (state == feasibility.end() || sequenceNewer(route.sequence, state->second.sequence))
        {
            feasibility[destination] = {route.sequence, route.distance};
        }
        else if (route.sequence == state->second.sequence &&
                 route.distance < state->second.feasibleDistance)
        {
            state->second.feasibleDistance = route.distance;
        }
    }
}

std::vector<NeighborRecordV2> CrystDatabase::getListOfRoutesV2() const
{
    std::vector<NeighborRecordV2> records;
    records.reserve(routeCache.size());
    for (const auto &entry : routeCache)
    {
        const RoutingRecord &route = entry.second;
        records.push_back({
            entry.first,
            route.router,
            route.sequence,
            route.distance});
    }
    std::sort(records.begin(), records.end(), [](const NeighborRecordV2 &a, const NeighborRecordV2 &b) {
        return a.id < b.id;
    });
    return records;
}

std::vector<NeighborRecord> CrystDatabase::getListOfNeighbours() const
{
    std::vector<NeighborRecord> records;
    records.reserve(routeCache.size());
    for (const auto &entry : routeCache)
    {
        records.push_back({
            entry.first,
            entry.second.router,
            entry.second.distance});
    }
    std::sort(records.begin(), records.end(), [](const NeighborRecord &a, const NeighborRecord &b) {
        return a.id < b.id;
    });
    return records;
}

std::vector<CrystSequenceRequest> CrystDatabase::takeSequenceRequests()
{
    std::vector<CrystSequenceRequest> result;
    result.swap(pendingSequenceRequests);
    return result;
}
