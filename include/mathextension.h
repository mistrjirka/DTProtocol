#pragma once
#include <stdint.h>
#include <random>
#include <cstdint>
class MathExtensionClass
{
private:
    void swap(int &a, int &b);
    int partition(int arr[], int start, int end);
    std::random_device rand_dev;
    std::mt19937 generator;
    std::uniform_int_distribution<int> distr;
public:
    MathExtensionClass(); // Constructor
    uint32_t crc32c(uint32_t crc, const unsigned char *buf, uint32_t len);

    void quickSort(int arr[], int start, int end);

    float timeOnAir(uint16_t sizeOfPacket, uint8_t preambleLength, uint8_t spreadingFactor, float bandwidth, uint8_t codingRate, uint8_t crcLength = 16, uint8_t nSymbolHeader = 20);
    int getRandomNumber(); // New function to generate random number
    void setRandomRange(int range_from, int range_to); // New function to set random range
};
extern class MathExtensionClass MathExtension;
