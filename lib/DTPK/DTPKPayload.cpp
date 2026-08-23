#include <DTPK.h>
#include <DTPKCompression.h>

#include <algorithm>
#include <cstring>
#include <cstdlib>
#include <limits>

static_assert(
    DTPK_COMPRESSION_HEATSHRINK_8_4 ==
        DTPKCompression::CODEC_HEATSHRINK_8_4,
    "compression codec identifiers must match the v4 wire contract");

uint64_t DTPK::estimatedReliablePayloadAirtimeMs(size_t payloadSize) const
{
    MAC *mac = MAC::getInstance();
    if (!mac)
        return std::numeric_limits<uint64_t>::max();

    const uint16_t ackFrameBytes = static_cast<uint16_t>(
        MAC_OVERHEAD + sizeof(LCMMPacketResponse) + sizeof(uint16_t));
    const uint64_t ackAirtime = mac->estimateFrameAirtimeMs(ackFrameBytes);

    uint64_t total = 0;
    if (payloadSize <= maximumSinglePayloadSize())
    {
        const uint16_t frameBytes = static_cast<uint16_t>(
            MAC_OVERHEAD + LCMM_OVERHEAD + sizeof(DTPKPacketGeneric) +
            payloadSize);
        return mac->estimateFrameAirtimeMs(frameBytes) + ackAirtime;
    }

    size_t remaining = payloadSize;
    while (remaining != 0)
    {
        const size_t chunk = std::min(remaining, fragmentPayloadSize());
        const uint16_t frameBytes = static_cast<uint16_t>(
            MAC_OVERHEAD + LCMM_OVERHEAD + sizeof(DTPKPacketFragment) +
            chunk);
        total += mac->estimateFrameAirtimeMs(frameBytes) + ackAirtime;
        remaining -= chunk;
    }
    return total;
}

bool DTPK::tryCompressPayload(
    const uint8_t *payload,
    size_t size,
    uint8_t *&encoded,
    size_t &encodedSize)
{
    encoded = nullptr;
    encodedSize = 0;

#if !DTPK_ENABLE_COMPRESSION
    (void)payload;
    (void)size;
    return false;
#else
    if (!payload || size < DTPK_COMPRESSION_MIN_INPUT_SIZE ||
        size > maximumMessageSize() || size > UINT16_MAX ||
        size <= sizeof(DTPKCompressedPayload))
        return false;

    ++_compressionDiagnostics.attempts;
    uint8_t *candidate = static_cast<uint8_t *>(malloc(size));
    if (!candidate)
    {
        ++_compressionDiagnostics.allocationFailures;
        return false;
    }

    DTPKCompressedPayload *prefix =
        reinterpret_cast<DTPKCompressedPayload *>(candidate);
    prefix->originalSize = static_cast<uint16_t>(size);
    prefix->codec = DTPK_COMPRESSION_HEATSHRINK_8_4;

    size_t compressedBytes = 0;
    const size_t capacity = size - sizeof(DTPKCompressedPayload);
    const DTPKCompression::CompressResult compressionResult =
        DTPKCompression::compress(
            payload,
            size,
            prefix->data,
            capacity,
            compressedBytes);
    if (compressionResult != DTPKCompression::CompressResult::Compressed)
    {
        if (compressionResult ==
            DTPKCompression::CompressResult::OutputTooSmall)
        {
            ++_compressionDiagnostics.candidateTooLarge;
        }
        else
        {
            ++_compressionDiagnostics.codecFailures;
            Serial.print("[DTPK] compression codec failure result=");
            Serial.println(static_cast<unsigned>(compressionResult));
        }
        free(candidate);
        return false;
    }

    if (!DTPKCompression::verify(
            prefix->data, compressedBytes, payload, size))
    {
        ++_compressionDiagnostics.verificationFailures;
        Serial.println(
            "[DTPK] compression self-check failed; sending original payload");
        free(candidate);
        return false;
    }

    const size_t candidateSize = sizeof(DTPKCompressedPayload) + compressedBytes;
    const uint64_t originalAirtime = estimatedReliablePayloadAirtimeMs(size);
    const uint64_t compressedAirtime =
        estimatedReliablePayloadAirtimeMs(candidateSize);
    if (compressedAirtime >= originalAirtime ||
        originalAirtime - compressedAirtime <
            static_cast<uint64_t>(DTPK_COMPRESSION_MIN_AIRTIME_SAVINGS_MS))
    {
        ++_compressionDiagnostics.noAirtimeBenefit;
        free(candidate);
        return false;
    }

    // Retain only the encoded bytes for multipart selective repair. A failed
    // shrink is harmless: realloc leaves the original candidate valid.
    void *smaller = realloc(candidate, candidateSize);
    if (smaller)
        candidate = static_cast<uint8_t *>(smaller);

    ++_compressionDiagnostics.selected;
    _compressionDiagnostics.originalBytes += size;
    _compressionDiagnostics.encodedBytes += candidateSize;
    _compressionDiagnostics.estimatedAirtimeSavedMs +=
        originalAirtime - compressedAirtime;
    encoded = candidate;
    encodedSize = candidateSize;
    return true;
#endif
}

bool DTPK::deliverApplicationPacket(
    DTPKPacketGeneric *packet,
    size_t packetSize)
{
    if (!packet || packetSize < sizeof(DTPKPacketGeneric))
        return false;

    if ((packet->flags & DTPK_FLAG_COMPRESSED) == 0)
    {
        if (_recieveCallback)
            _recieveCallback(
                packet,
                static_cast<uint16_t>(
                    std::min<size_t>(packetSize, UINT16_MAX)));
        return true;
    }

    const size_t payloadSize = packetSize - sizeof(DTPKPacketGeneric);
    if (payloadSize <= sizeof(DTPKCompressedPayload))
    {
        ++_compressionDiagnostics.decodeFailures;
        Serial.println("[DTPK] rejected truncated compression envelope");
        return false;
    }

    const DTPKCompressedPayload *prefix =
        reinterpret_cast<const DTPKCompressedPayload *>(packet->data);
    if (prefix->codec != DTPK_COMPRESSION_HEATSHRINK_8_4 ||
        prefix->originalSize == 0 ||
        prefix->originalSize > maximumMessageSize())
    {
        ++_compressionDiagnostics.decodeFailures;
        Serial.print("[DTPK] rejected compression envelope codec=");
        Serial.print(static_cast<unsigned>(prefix->codec));
        Serial.print(" size=");
        Serial.println(static_cast<unsigned>(prefix->originalSize));
        return false;
    }

    const size_t decodedPacketSize =
        sizeof(DTPKPacketGeneric) + prefix->originalSize;
    DTPKPacketGeneric *decoded = static_cast<DTPKPacketGeneric *>(
        malloc(decodedPacketSize));
    if (!decoded)
    {
        ++_compressionDiagnostics.allocationFailures;
        Serial.print("[DTPK] cannot allocate decompression buffer bytes=");
        Serial.println(static_cast<unsigned>(prefix->originalSize));
        return false;
    }

    memcpy(decoded, packet, sizeof(DTPKPacketGeneric));
    decoded->flags = static_cast<uint8_t>(
        decoded->flags & ~static_cast<uint8_t>(DTPK_FLAG_COMPRESSED));
    const size_t compressedBytes =
        payloadSize - sizeof(DTPKCompressedPayload);
    const bool ok = DTPKCompression::decompress(
        prefix->data,
        compressedBytes,
        decoded->data,
        prefix->originalSize);
    if (!ok)
    {
        ++_compressionDiagnostics.decodeFailures;
        Serial.print("[DTPK] decompression failed sender=");
        Serial.print(static_cast<unsigned>(packet->originalSender));
        Serial.print(" id=");
        Serial.print(static_cast<unsigned>(packet->id));
        Serial.print(" encoded=");
        Serial.print(static_cast<unsigned>(compressedBytes));
        Serial.print(" expected=");
        Serial.println(static_cast<unsigned>(prefix->originalSize));
        free(decoded);
        return false;
    }

    if (_recieveCallback)
        _recieveCallback(
            decoded,
            static_cast<uint16_t>(decodedPacketSize));
    free(decoded);
    return true;
}
