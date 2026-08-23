#include <DTPKCompression.h>

extern "C"
{
#include "heatshrink/heatshrink_decoder.h"
#include "heatshrink/heatshrink_encoder.h"
}

#include <cstring>

namespace
{
static_assert(HEATSHRINK_DYNAMIC_ALLOC == 0,
              "DTProtocol requires static heatshrink allocation");
static_assert(HEATSHRINK_STATIC_WINDOW_BITS == DTPKCompression::WINDOW_BITS,
              "heatshrink window is part of the v4 wire contract");
static_assert(
    HEATSHRINK_STATIC_LOOKAHEAD_BITS == DTPKCompression::LOOKAHEAD_BITS,
    "heatshrink lookahead is part of the v4 wire contract");
static_assert(HEATSHRINK_USE_INDEX == 1,
              "DTProtocol expects the bounded indexed encoder");

heatshrink_encoder encoderState;
heatshrink_decoder decoderState;

struct EncoderSnapshot
{
    uint16_t inputSize;
    uint16_t matchScanIndex;
    uint16_t outgoingBits;
    uint8_t outgoingBitsCount;
    uint8_t state;
    uint8_t bitIndex;
};

EncoderSnapshot encoderSnapshot()
{
    return EncoderSnapshot{
        encoderState.input_size,
        encoderState.match_scan_index,
        encoderState.outgoing_bits,
        encoderState.outgoing_bits_count,
        encoderState.state,
        encoderState.bit_index};
}

bool encoderChanged(const EncoderSnapshot &before)
{
    return before.inputSize != encoderState.input_size ||
           before.matchScanIndex != encoderState.match_scan_index ||
           before.outgoingBits != encoderState.outgoing_bits ||
           before.outgoingBitsCount != encoderState.outgoing_bits_count ||
           before.state != encoderState.state ||
           before.bitIndex != encoderState.bit_index;
}

DTPKCompression::CompressResult pollEncoder(
    uint8_t *output,
    size_t outputCapacity,
    size_t &outputSize,
    bool &madeProgress)
{
    while (true)
    {
        // A candidate that exactly fills its bounded buffer can never be useful
        // once the compression envelope is added, so fail closed instead of
        // polling with a zero-sized output buffer.
        if (outputSize >= outputCapacity)
            return DTPKCompression::CompressResult::OutputTooSmall;

        const EncoderSnapshot before = encoderSnapshot();
        size_t produced = 0;
        const HSE_poll_res result = heatshrink_encoder_poll(
            &encoderState,
            output + outputSize,
            outputCapacity - outputSize,
            &produced);
        if (result < 0)
            return DTPKCompression::CompressResult::CodecError;
        outputSize += produced;
        madeProgress = madeProgress || produced != 0 || encoderChanged(before);
        if (result == HSER_POLL_EMPTY)
            return DTPKCompression::CompressResult::Compressed;
        if (produced == 0 && !encoderChanged(before))
            return DTPKCompression::CompressResult::CodecError;
    }
}

struct DecoderSnapshot
{
    uint16_t inputSize;
    uint16_t inputIndex;
    uint16_t outputCount;
    uint16_t outputIndex;
    uint16_t headIndex;
    uint8_t state;
    uint8_t bitIndex;
};

DecoderSnapshot decoderSnapshot()
{
    return DecoderSnapshot{
        decoderState.input_size,
        decoderState.input_index,
        decoderState.output_count,
        decoderState.output_index,
        decoderState.head_index,
        decoderState.state,
        decoderState.bit_index};
}

bool decoderChanged(const DecoderSnapshot &before)
{
    return before.inputSize != decoderState.input_size ||
           before.inputIndex != decoderState.input_index ||
           before.outputCount != decoderState.output_count ||
           before.outputIndex != decoderState.output_index ||
           before.headIndex != decoderState.head_index ||
           before.state != decoderState.state ||
           before.bitIndex != decoderState.bit_index;
}

template <typename Consumer>
bool decodeStream(
    const uint8_t *input,
    size_t inputSize,
    size_t expectedSize,
    Consumer consume)
{
    if ((!input && inputSize != 0) || expectedSize == 0)
        return false;

    heatshrink_decoder_reset(&decoderState);
    size_t sunk = 0;
    size_t decoded = 0;
    uint8_t chunk[64];
    size_t guard = 0;
    const size_t guardLimit = (inputSize + expectedSize + 1u) * 8u + 128u;

    while (true)
    {
        const DecoderSnapshot loopBefore = decoderSnapshot();
        bool madeProgress = false;
        if (sunk < inputSize)
        {
            size_t consumed = 0;
            const HSD_sink_res sinkResult = heatshrink_decoder_sink(
                &decoderState,
                const_cast<uint8_t *>(input + sunk),
                inputSize - sunk,
                &consumed);
            if (sinkResult < 0)
                return false;
            sunk += consumed;
            madeProgress = consumed != 0;
        }

        while (true)
        {
            const DecoderSnapshot before = decoderSnapshot();
            size_t produced = 0;
            const HSD_poll_res pollResult = heatshrink_decoder_poll(
                &decoderState, chunk, sizeof(chunk), &produced);
            if (pollResult < 0)
                return false;
            if (produced != 0)
            {
                if (decoded > expectedSize || produced > expectedSize - decoded)
                    return false;
                if (!consume(chunk, produced, decoded))
                    return false;
                decoded += produced;
            }
            madeProgress = madeProgress || produced != 0 || decoderChanged(before);
            if (pollResult == HSDR_POLL_EMPTY)
                break;
            if (produced == 0 && !decoderChanged(before))
                return false;
        }

        if (sunk == inputSize)
        {
            const HSD_finish_res finishResult =
                heatshrink_decoder_finish(&decoderState);
            if (finishResult < 0)
                return false;
            if (finishResult == HSDR_FINISH_DONE)
                return decoded == expectedSize;
        }

        madeProgress = madeProgress || decoderChanged(loopBefore);
        if (!madeProgress || ++guard > guardLimit)
            return false;
    }
}
} // namespace

namespace DTPKCompression
{
CompressResult compress(
    const uint8_t *input,
    size_t inputSize,
    uint8_t *output,
    size_t outputCapacity,
    size_t &outputSize)
{
    outputSize = 0;
    if (!input || inputSize == 0 || !output || outputCapacity == 0)
        return CompressResult::InvalidArgument;

    heatshrink_encoder_reset(&encoderState);
    size_t sunk = 0;
    size_t guard = 0;
    const size_t guardLimit = (inputSize + 1u) * 8u + 128u;

    while (sunk < inputSize)
    {
        const EncoderSnapshot loopBefore = encoderSnapshot();
        size_t consumed = 0;
        const HSE_sink_res sinkResult = heatshrink_encoder_sink(
            &encoderState,
            const_cast<uint8_t *>(input + sunk),
            inputSize - sunk,
            &consumed);
        if (sinkResult < 0)
            return CompressResult::CodecError;
        sunk += consumed;

        bool madeProgress = consumed != 0;
        const CompressResult pollResult =
            pollEncoder(output, outputCapacity, outputSize, madeProgress);
        if (pollResult != CompressResult::Compressed)
            return pollResult;
        madeProgress = madeProgress || encoderChanged(loopBefore);
        if (!madeProgress || ++guard > guardLimit)
            return CompressResult::CodecError;
    }

    while (true)
    {
        const EncoderSnapshot loopBefore = encoderSnapshot();
        const HSE_finish_res finishResult =
            heatshrink_encoder_finish(&encoderState);
        if (finishResult < 0)
            return CompressResult::CodecError;

        bool madeProgress = false;
        const CompressResult pollResult =
            pollEncoder(output, outputCapacity, outputSize, madeProgress);
        if (pollResult != CompressResult::Compressed)
            return pollResult;
        if (finishResult == HSER_FINISH_DONE)
            return outputSize != 0
                       ? CompressResult::Compressed
                       : CompressResult::CodecError;
        madeProgress = madeProgress || encoderChanged(loopBefore);
        if (!madeProgress || ++guard > guardLimit)
            return CompressResult::CodecError;
    }
}

bool decompress(
    const uint8_t *input,
    size_t inputSize,
    uint8_t *output,
    size_t expectedSize)
{
    if (!output)
        return false;
    return decodeStream(
        input,
        inputSize,
        expectedSize,
        [output](const uint8_t *chunk, size_t size, size_t offset) {
            memcpy(output + offset, chunk, size);
            return true;
        });
}

bool verify(
    const uint8_t *compressed,
    size_t compressedSize,
    const uint8_t *original,
    size_t originalSize)
{
    if (!original)
        return false;
    return decodeStream(
        compressed,
        compressedSize,
        originalSize,
        [original](const uint8_t *chunk, size_t size, size_t offset) {
            return memcmp(original + offset, chunk, size) == 0;
        });
}

size_t encoderWorkspaceBytes()
{
    return sizeof(encoderState);
}

size_t decoderWorkspaceBytes()
{
    return sizeof(decoderState);
}
} // namespace DTPKCompression
