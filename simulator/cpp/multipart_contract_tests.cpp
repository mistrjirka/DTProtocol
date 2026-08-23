#include <DTPKDefinitions.h>
#include <generalsettings.h>

#include <cassert>
#include <cstddef>

int main()
{
    static_assert(DTPK_WIRE_VERSION == 0x40u, "v4 wire prefix changed");
    static_assert(sizeof(DTPKCompressedPayload) == 3,
                  "compression envelope changed");
    static_assert(DATASIZE_LCMM == 244, "LCMM payload capacity changed");
    static_assert(sizeof(DTPKPacketGeneric) == 11, "single-data header changed");
    static_assert(sizeof(DTPKPacketFragment) == 14, "fragment header changed");
    static_assert(sizeof(DTPKPacketFragmentQuery) == 15, "query header changed");
    static_assert(sizeof(DTPKPacketFragmentStatus) == 14, "status header changed");

    constexpr size_t singlePayload =
        DATASIZE_LCMM - sizeof(DTPKPacketGeneric);
    constexpr size_t fragmentPayload =
        DATASIZE_LCMM - sizeof(DTPKPacketFragment);
    static_assert(singlePayload == 233, "single-frame payload changed");
    static_assert(fragmentPayload == 230, "fragment payload changed");

    assert((1000u + fragmentPayload - 1u) / fragmentPayload == 5u);
    assert((1800u + fragmentPayload - 1u) / fragmentPayload == 8u);
    assert(DTPK_MAX_MESSAGE_SIZE == 16u * 1024u);

    constexpr size_t maxFragments =
        (DTPK_MAX_MESSAGE_SIZE + fragmentPayload - 1u) / fragmentPayload;
    static_assert(maxFragments == 72, "default 16 KiB fragment count changed");
    constexpr size_t bitmapBytes = (maxFragments + 7u) / 8u;
    static_assert(bitmapBytes == 9, "default status bitmap changed");
    static_assert(
        MAC_OVERHEAD + LCMM_OVERHEAD + sizeof(DTPKPacketFragmentStatus) +
                bitmapBytes ==
            34,
        "16 KiB selective status frame should remain 34 radio bytes");
    return 0;
}
