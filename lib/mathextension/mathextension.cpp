#include "include/mathextension.h"

#define max(a, b) \
    ({ __typeof__ (a) _a = (a); \
       __typeof__ (b) _b = (b); \
     _a > _b ? _a : _b; })
/* CRC-32C (iSCSI) polynomial in reversed bit order. */
#define POLY 0x82f63b78

/* CRC-32 (Ethernet, ZIP, etc.) polynomial in reversed bit order. */
/* #define POLY 0xedb88320 */

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
int MathExtensionClass::partition(int arr[], int start, int end)
{

    int pivot = arr[start];

    int count = 0;
    for (int i = start + 1; i <= end; i++)
    {
        if (arr[i] <= pivot)
            count++;
    }

    // Giving pivot element its correct position
    int pivotIndex = start + count;
    swap(arr[pivotIndex], arr[start]);

    // Sorting left and right parts of the pivot element
    int i = start, j = end;

    while (i < pivotIndex && j > pivotIndex)
    {

        while (arr[i] <= pivot)
        {
            i++;
        }

        while (arr[j] > pivot)
        {
            j--;
        }

        if (i < pivotIndex && j > pivotIndex)
        {
            swap(arr[i++], arr[j--]);
        }
    }

    return pivotIndex;
}

void MathExtensionClass::quickSort(int arr[], int start, int end)
{

    // base case
    if (start >= end)
        return;

    // partitioning the array
    int p = partition(arr, start, end);

    // Sorting the left part
    quickSort(arr, start, p - 1);

    // Sorting the right part
    quickSort(arr, p + 1, end);
}

float MathExtensionClass::timeOnAir(uint16_t sizeOfPacket, uint8_t preambleLength, uint8_t spreadingFactor, float bandwidth, uint8_t codingRate, uint8_t crcLength, uint8_t nSymbolHeader)
{
    float symbolTime = (float)pow(2, spreadingFactor) / bandwidth;
    bool LDO = symbolTime > 16;
    float numOfSymbols = 0;

    if (spreadingFactor >= 5 && spreadingFactor <= 6)
    {
        numOfSymbols = (float)preambleLength + 6.25 + 8.0 + (float)ceil((float)max(8 * sizeOfPacket + crcLength - 4 * spreadingFactor + nSymbolHeader, 0) / (4 * spreadingFactor)) * (codingRate);
    }else if(LDO){
        numOfSymbols = (float)preambleLength + 4.25 + 8.0 + (float)ceil((float)max(8 * sizeOfPacket + crcLength - 4 * spreadingFactor + nSymbolHeader, 0) / (4 * (spreadingFactor - 2))) * (codingRate);
    }else{
        numOfSymbols = (float)preambleLength + 4.25 + 8.0 + (float)ceil((float)max(8 * sizeOfPacket + crcLength - 4 * spreadingFactor + nSymbolHeader, 0) / (4 * (spreadingFactor))) * (codingRate);
    }
    return numOfSymbols * symbolTime;
}

int MathExtensionClass::getRandomNumber(int range_from, int range_to)
{
 return random(range_from, range_to);
}




MathExtensionClass MathExtension;