#ifndef DTPK_COMPRESSION_H
#define DTPK_COMPRESSION_H

#include <cstddef>
#include <cstdint>

namespace DTPKCompression
{
// Heatshrink parameters are part of the v4 wire contract.
static constexpr uint8_t CODEC_HEATSHRINK_8_4 = 1;
static constexpr uint8_t WINDOW_BITS = 8;
static constexpr uint8_t LOOKAHEAD_BITS = 4;

enum class CompressResult : uint8_t
{
    Compressed = 0,
    OutputTooSmall,
    InvalidArgument,
    CodecError
};

// Compresses into a caller-owned bounded buffer. OutputTooSmall is an expected
// result for incompressible data; callers should transmit the original bytes.
CompressResult compress(
    const uint8_t *input,
    size_t inputSize,
    uint8_t *output,
    size_t outputCapacity,
    size_t &outputSize);

// Decodes exactly expectedSize bytes. Truncated streams, extra decoded bytes,
// malformed back-references and length mismatches all return false.
bool decompress(
    const uint8_t *input,
    size_t inputSize,
    uint8_t *output,
    size_t expectedSize);

// Source-side safety gate: decode a candidate and compare it byte-for-byte with
// the original without allocating a second message-sized buffer.
bool verify(
    const uint8_t *compressed,
    size_t compressedSize,
    const uint8_t *original,
    size_t originalSize);

size_t encoderWorkspaceBytes();
size_t decoderWorkspaceBytes();
} // namespace DTPKCompression

#endif
