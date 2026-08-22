#include "include/mathextension.h"

#include <algorithm>
#include <cmath>

/* CRC-32C (iSCSI) polynomial in reversed bit order. */
#define POLY 0x82f63b78

uint32_t MathExtensionClass::crc32c(uint32_t crc, const unsigned char *buf, uint32_t len)
{
    int k;

    crc = ~crc;
    while (len--)
    {
        crc ^= *buf++;
        for (k = 0; k < 8; k++)
            crc = crc & 1 ? (crc >> 1) ^ POLY : crc >> 1;
    }

    return ~crc;
}

void MathExtensionClass::swap(int &a, int &b)
{
    int t = a;
    a = b;
    b = t;
}

uint64_t MathExtensionClass::murmur64(uint64_t input)
{
    const uint64_t m = 0xc6a4a7935bd1e995ull;
    const int r = 47;

    uint64_t h = 0x9368e53c2f6af274ull ^ (sizeof(uint64_t) * m);

    input *= m;
    input ^= input >> r;
    input *= m;
    h ^= input;
    h *= m;

    h ^= h >> r;
    h *= m;
    h ^= h >> r;

    return h;
}

int MathExtensionClass::partition(int arr[], int start, int end)
{
    const int pivot = arr[start];

    int count = 0;
    for (int index = start + 1; index <= end; ++index)
    {
        if (arr[index] <= pivot)
            ++count;
    }

    const int pivotIndex = start + count;
    swap(arr[pivotIndex], arr[start]);

    int i = start;
    int j = end;
    while (i < pivotIndex && j > pivotIndex)
    {
        // The original implementation omitted these bounds and could read one
        // element past the partition when the pivot was an extremum.
        while (i < pivotIndex && arr[i] <= pivot)
            ++i;
        while (j > pivotIndex && arr[j] > pivot)
            --j;

        if (i < pivotIndex && j > pivotIndex)
            swap(arr[i++], arr[j--]);
    }

    return pivotIndex;
}

void MathExtensionClass::quickSort(int arr[], int start, int end)
{
    if (start >= end)
        return;

    const int pivot = partition(arr, start, end);
    quickSort(arr, start, pivot - 1);
    quickSort(arr, pivot + 1, end);
}

float MathExtensionClass::timeOnAir(
    uint16_t sizeOfPacket,
    uint8_t preambleLength,
    uint8_t spreadingFactor,
    float bandwidth,
    uint8_t codingRate,
    uint8_t crcLength,
    uint8_t nSymbolHeader)
{
    if (bandwidth <= 0.0f || spreadingFactor < 5 || spreadingFactor > 12)
        return 0.0f;

    // bandwidth is supplied in kHz, so symbolTime is directly in ms.
    const float symbolTime =
        static_cast<float>(1UL << spreadingFactor) / bandwidth;
    // RadioLib autoLDRO() enables optimization when symbol length is >=16 ms.
    const bool lowDataRateOptimize = symbolTime >= 16.0f;

    // Match the SX126x LoRa time-on-air equation used by RadioLib. For SF5/6
    // the preamble coefficient and the +8 term differ from SF7-12.
    const float preambleExtra = spreadingFactor <= 6 ? 6.25f : 4.25f;
    const int sfCoefficient2 = spreadingFactor <= 6 ? 0 : 8;
    const int divisor =
        4 * (static_cast<int>(spreadingFactor) -
             (lowDataRateOptimize ? 2 : 0));

    int bitCount =
        8 * static_cast<int>(sizeOfPacket) +
        static_cast<int>(crcLength) -
        4 * static_cast<int>(spreadingFactor) +
        sfCoefficient2 +
        static_cast<int>(nSymbolHeader);
    bitCount = std::max(bitCount, 0);

    const int preCodedSymbols =
        (bitCount + divisor - 1) / divisor;
    const float symbols =
        static_cast<float>(preambleLength) +
        preambleExtra +
        8.0f +
        static_cast<float>(preCodedSymbols * codingRate);

    return symbols * symbolTime;
}

int MathExtensionClass::getRandomNumber(int range_from, int range_to)
{
    return random(range_from, range_to);
}

MathExtensionClass MathExtension;
