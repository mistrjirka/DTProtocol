#pragma once
#include <stdint.h>

#include <cstdint>
class MathExtensionClass
{
private:
    void swap(int &a, int &b);
    int partition(int arr[], int start, int end);
public:
    uint32_t crc32c(uint32_t crc, const unsigned char *buf, uint32_t len);

    void quickSort(int arr[], int start, int end);
};
extern class MathExtensionClass MathExtension;
