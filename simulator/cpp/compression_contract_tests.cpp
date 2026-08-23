#include <DTPKCompression.h>

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace
{
void roundTrip(const std::vector<uint8_t> &input)
{
    assert(!input.empty());
    std::vector<uint8_t> compressed(input.size() + input.size() / 4u + 16u);
    size_t compressedSize = 0;
    assert(DTPKCompression::compress(
        input.data(), input.size(), compressed.data(), compressed.size(),
        compressedSize) == DTPKCompression::CompressResult::Compressed);
    assert(compressedSize > 0);
    assert(compressedSize <= compressed.size());
    assert(DTPKCompression::verify(
        compressed.data(), compressedSize, input.data(), input.size()));

    std::vector<uint8_t> decoded(input.size());
    assert(DTPKCompression::decompress(
        compressed.data(), compressedSize, decoded.data(), decoded.size()));
    assert(decoded == input);

    if (compressedSize > 1)
    {
        assert(!DTPKCompression::decompress(
            compressed.data(), compressedSize - 1u,
            decoded.data(), decoded.size()));
    }
    assert(!DTPKCompression::decompress(
        compressed.data(), compressedSize, decoded.data(), decoded.size() - 1u));
    assert(!DTPKCompression::decompress(
        compressed.data(), compressedSize, decoded.data(), decoded.size() + 1u));
}
} // namespace

int main()
{
    assert(DTPKCompression::encoderWorkspaceBytes() <= 2048u);
    assert(DTPKCompression::decoderWorkspaceBytes() <= 512u);

    const std::string text =
        "Picopod mesh message: the same route and status fields repeat. ";
    std::vector<uint8_t> repeated;
    for (int i = 0; i < 80; ++i)
        repeated.insert(repeated.end(), text.begin(), text.end());
    roundTrip(repeated);

    roundTrip(std::vector<uint8_t>(16u * 1024u, 0));

    std::mt19937 generator(0x51a7c0deu);
    for (size_t size = 1; size <= 16u * 1024u; size = size < 256u ? size + 1u : size * 2u)
    {
        std::vector<uint8_t> randomBytes(size);
        for (uint8_t &value : randomBytes)
            value = static_cast<uint8_t>(generator());
        roundTrip(randomBytes);
    }

    // Malformed streams must terminate safely for arbitrary expected lengths.
    // The result may be true by chance, but ASan/UBSan must never observe an
    // overrun, invalid back-reference or non-terminating decoder state.
    for (size_t iteration = 0; iteration < 5000; ++iteration)
    {
        const size_t encodedSize = 1u + generator() % 96u;
        const size_t expectedSize = 1u + generator() % 4096u;
        std::vector<uint8_t> malformed(encodedSize);
        for (uint8_t &value : malformed)
            value = static_cast<uint8_t>(generator());
        std::vector<uint8_t> destination(expectedSize, 0xA5u);
        (void)DTPKCompression::decompress(
            malformed.data(), malformed.size(),
            destination.data(), destination.size());
    }

    // Bounded compression must fail closed when an incompressible candidate is
    // not allowed enough room; callers then transmit the original bytes.
    std::vector<uint8_t> randomBytes(1024);
    for (uint8_t &value : randomBytes)
        value = static_cast<uint8_t>(generator());
    std::array<uint8_t, 32> tiny{};
    size_t outputSize = 123;
    assert(DTPKCompression::compress(
        randomBytes.data(), randomBytes.size(), tiny.data(), tiny.size(),
        outputSize) == DTPKCompression::CompressResult::OutputTooSmall);

    return 0;
}
