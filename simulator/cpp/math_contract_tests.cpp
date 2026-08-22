#include <mathextension.h>

#include <cassert>
#include <cmath>

int main() {
    // Extremal/equal pivots used to let partition() advance beyond pivotIndex.
    int descending[] = {10, 9, 8, 7, 6, 5, 4, 3, 2, 1};
    MathExtension.quickSort(descending, 0, 9);
    for (int i = 0; i < 10; ++i)
        assert(descending[i] == i + 1);

    int equal[] = {5, 5, 5, 5, 5, 5, 5, 5};
    MathExtension.quickSort(equal, 0, 7);
    for (int value : equal)
        assert(value == 5);

    // SX126x/RadioLib equation, explicit header + CRC, SF9/BW125/CR4/7.
    // Four bytes is a useful boundary: omitting the SX126x +8 term produces
    // 111.616 ms instead of the correct 140.288 ms.
    const float toa = MathExtension.timeOnAir(4, 8, 9, 125.0f, 7);
    assert(std::fabs(toa - 140.288f) < 0.01f);

    return 0;
}
