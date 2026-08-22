#include <CrystDatabase.h>

#include <cassert>

int main() {
    CrystDatabase db(1);

    NeighborRecordV2 staleShort{4, 1, 1};
    assert(db.updateDirectNeighbor(2, 10, true));
    assert(db.updateFromCrystPacket(2, 10, &staleShort, 1));
    db.noteAdvertisedRoutes();

    RoutingRecord *route = db.getRouting(4);
    assert(route != nullptr);
    assert(route->router == 2);
    assert(route->distance == 2);
    assert(route->sequence == 1);

    // A newer generation makes this candidate feasible, but sequence freshness
    // is not a route metric. The still-feasible shorter candidate remains best.
    NeighborRecordV2 freshLong{4, 2, 3};
    assert(db.updateDirectNeighbor(3, 11, true));
    assert(!db.updateFromCrystPacket(3, 11, &freshLong, 1));

    route = db.getRouting(4);
    assert(route != nullptr);
    assert(route->router == 2);
    assert(route->distance == 2);
    assert(route->sequence == 1);

    // Repair selection prefers a normal feasible successor. Sequence freshness
    // makes a candidate feasible; it is not a repair-path metric either.
    uint16_t repair = 0;
    assert(db.getRepairNextHop(4, 0, repair));
    assert(repair == 2);
    assert(db.getRepairNextHop(4, 3, repair));
    assert(repair == 2);

    // After the stale short contribution is withdrawn, the newer longer route
    // is selected by the ordinary metric rule.
    assert(db.removeNeighbor(2));
    route = db.getRouting(4);
    assert(route != nullptr);
    assert(route->router == 3);
    assert(route->distance == 4);
    assert(route->sequence == 2);

    return 0;
}
