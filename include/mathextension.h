#include <stdint.h>
#include <stddef.h>
#include <cstdio>
#include <math.h>
#include <cstdint>
#include <Arduino.h>
#ifndef MATHEXTENSION_H
#define MATHEXTENSION_H

class MathExtensionClass
{
private:
    void swap(int &a, int &b);
    int partition(int arr[], int start, int end);

public:
    uint32_t crc32c(uint32_t crc, const unsigned char *buf, uint32_t len);

    void quickSort(int arr[], int start, int end);
    uint64_t murmur64(uint64_t h);
    float timeOnAir(uint16_t sizeOfPacket, uint8_t preambleLength, uint8_t spreadingFactor, float bandwidth, uint8_t codingRate, uint8_t crcLength = 16, uint8_t nSymbolHeader = 20);
    int getRandomNumber(int range_from, int range_to);
};
extern class MathExtensionClass MathExtension;

#endif