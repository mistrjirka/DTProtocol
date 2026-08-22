#include <CrystDatabase.h>

#include <cassert>

int main() {
    CrystDatabase db(1);

    NeighborRecordV2 staleShort{4, 4, 1, 1};
    assert(db.updateDirectNeighbor(2, 10, true));
    assert(db.updateFromCrystPacket(2, 10, &staleShort, 1));
    db.noteAdvertisedRoutes();

    RoutingRecord *route = db.getRouting(4);
    assert(route != nullptr);
    assert(route->router == 2);
    assert(route->distance == 2);
    assert(route->sequence == 1);

    // Generation 2 is newer destination state and must supersede generation 1,
    // even though its currently available path is longer.
    NeighborRecordV2 freshLong{4, 4, 2, 3};
    assert(db.updateDirectNeighbor(3, 11, true));
    assert(db.updateFromCrystPacket(3, 11, &freshLong, 1));

    route = db.getRouting(4);
    assert(route != nullptr);
    assert(route->router == 3);
    assert(route->distance == 4);
    assert(route->sequence == 2);

    return 0;
}
