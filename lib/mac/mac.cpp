#include "include/mac.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

State MAC::state = RECEIVING;
MAC *MAC::mac = nullptr;
volatile bool MAC::operationDone = false;

// 125 kHz-safe channel centres. The old edge channels at 433.05 and 434.8
// cannot fit a 125 kHz LoRa signal wholly inside 433.05-434.79 MHz.
const double MAC::EU433_CHANNELS[] = {
    433.175, 433.300, 433.425, 433.550, 433.675, 433.800, 433.925,
    434.050, 434.175, 434.300, 434.425, 434.550, 434.675};
const double MAC::EU868_CHANNELS[] = {868.100, 868.300, 868.500};
const uint8_t MAC::EU433_CHANNEL_COUNT = 13;
const uint8_t MAC::EU868_CHANNEL_COUNT = 3;

namespace
{
bool radioOk(int status)
{
  if (status == RADIOLIB_ERR_NONE)
    return true;
  Serial.println("RadioLib configuration error: " + String(status));
  return false;
}
} // namespace

MAC::MAC(
    SX1262 &loramodule,
    int nodeId,
    MACRegion selectedRegion,
    int defaultChannel,
    int defaultSpreadingFactor,
    float defaultBandwidth,
    int selectedSquelch,
    int defaultPower,
    int defaultCodingRate)
    : module(loramodule),
      channels(selectedRegion == MACRegion::EU868 ? EU868_CHANNELS : EU433_CHANNELS),
      channelCount(selectedRegion == MACRegion::EU868 ? EU868_CHANNEL_COUNT : EU433_CHANNEL_COUNT),
      region(selectedRegion),
      // 868.0-868.6 permits 25 mW e.r.p. in the conservative Czech/EU profile.
      // 13 dBm conducted leaves headroom for antenna gain until the RF path is
      // characterised. EU433 stays at the traditional 10 dBm conducted cap.
      maxConductedPowerDbm(selectedRegion == MACRegion::EU868 ? 13 : 10),
      id(static_cast<uint16_t>(nodeId)),
      channel(defaultChannel),
      spreading_factor(defaultSpreadingFactor),
      bandwidth(defaultBandwidth),
      squelch(selectedSquelch),
      power(std::min(defaultPower, maxConductedPowerDbm)),
      coding_rate(defaultCodingRate),
      calibratedFrequency(0.0),
      transmitDone(nullptr),
      RXCallback(nullptr),
      RXAlienCallback(nullptr),
      carrierBackoffUntil(0),
      dutyCycleUntil(0)
{
  memset(noiseFloor, 0, sizeof(noiseFloor));

  if (!validChannel(static_cast<uint16_t>(channel)) ||
      !configureRadio(spreading_factor, bandwidth, power, coding_rate))
  {
    // Leave an unmistakably invalid channel marker. sendData() will fail
    // instead of pretending a partially configured radio is usable.
    channel = -1;
    state = IDLE;
    return;
  }

  module.setDio1Action(setFlag);
  LORANoiseCalibrateAllChannels(true);
  setFrequency(static_cast<uint16_t>(channel));
  setMode(RECEIVING, true);
}

MAC::~MAC() {}

void MAC::initialize(
    SX1262 &loramodule,
    int id,
    int default_channel,
    int default_spreading_factor,
    float default_bandwidth,
    int squelch,
    int default_power,
    int default_coding_rate)
{
  initialize(
      loramodule,
      id,
      MACRegion::EU433,
      default_channel,
      default_spreading_factor,
      default_bandwidth,
      squelch,
      default_power,
      default_coding_rate);
}

void MAC::initialize(
    SX1262 &loramodule,
    int id,
    MACRegion region,
    int default_channel,
    int default_spreading_factor,
    float default_bandwidth,
    int squelch,
    int default_power,
    int default_coding_rate)
{
  if (mac == nullptr)
  {
    mac = new MAC(
        loramodule,
        id,
        region,
        default_channel,
        default_spreading_factor,
        default_bandwidth,
        squelch,
        default_power,
        default_coding_rate);
  }
}

MAC *MAC::getInstance()
{
  return mac;
}

bool MAC::validChannel(uint16_t candidate) const
{
  return candidate < channelCount;
}

bool MAC::configureRadio(
    int defaultSpreadingFactor,
    float defaultBandwidth,
    int defaultPower,
    int defaultCodingRate)
{
  if (!validChannel(static_cast<uint16_t>(channel)))
    return false;

  calibratedFrequency = channels[channel];
  bool ok = true;
  ok = radioOk(module.setFrequency(static_cast<float>(calibratedFrequency))) && ok;
  ok = radioOk(module.setOutputPower(defaultPower)) && ok;
  ok = radioOk(module.setBandwidth(defaultBandwidth)) && ok;
  ok = radioOk(module.setSpreadingFactor(defaultSpreadingFactor)) && ok;
  ok = radioOk(module.setCodingRate(defaultCodingRate)) && ok;
  ok = radioOk(module.setSyncWord(DEFAULT_SYNC_WORD)) && ok;
  ok = radioOk(module.setPreambleLength(DEFAULT_PREAMBLE_LENGTH)) && ok;
  return ok;
}

uint16_t MAC::getId()
{
  return id;
}

uint32_t MAC::random()
{
  return module.random(65000);
}

uint8_t MAC::getNumberOfChannels()
{
  return channelCount;
}

int MAC::getNoiseFloorOfChannel(uint8_t channel_num)
{
  if (!validChannel(channel_num))
    return 255;
  return noiseFloor[channel_num];
}

void MAC::setRXCallback(PacketReceivedCallback callback)
{
  RXCallback = callback;
}

void MAC::setRXAlienCallback(PacketReceivedCallback callback)
{
  RXAlienCallback = callback;
}

void MAC::setTransmitDone(TransmitDone callback)
{
  transmitDone = callback;
}

bool MAC::deadlinePending(uint32_t now, uint32_t deadline)
{
  return static_cast<int32_t>(now - deadline) < 0;
}

uint32_t MAC::getTransmitWaitMs() const
{
  const uint32_t now = millis();
  uint32_t wait = 0;
  if (deadlinePending(now, carrierBackoffUntil))
    wait = carrierBackoffUntil - now;
  if (region == MACRegion::EU868 && deadlinePending(now, dutyCycleUntil))
    wait = std::max(wait, dutyCycleUntil - now);
  return wait;
}

void MAC::startCarrierBackoff()
{
  // About 6-60 SF9/BW125 symbols. The randomisation is intentionally much
  // larger than a single CAD operation so peers that observed the same busy
  // packet do not all re-enter at the same instant.
  const uint32_t delayMs = 25u + module.random(226u);
  carrierBackoffUntil = millis() + delayMs;
}

void MAC::accountDutyCycle(uint8_t packetLength)
{
  if (region != MACRegion::EU868)
    return;

  const float airtimeMs = MathExtension.timeOnAir(
      packetLength,
      DEFAULT_PREAMBLE_LENGTH,
      static_cast<uint8_t>(spreading_factor),
      bandwidth,
      static_cast<uint8_t>(coding_rate));

  if (!(airtimeMs > 0.0f) || !std::isfinite(airtimeMs))
    return;

  // Conservative 1% policy: two transmissions may start no closer than
  // 100*ToA. This is deliberately stricter than a burst-capable hourly token
  // bucket and, unlike the old LBT loop, never blocks the cooperative firmware.
  const double period = std::ceil(static_cast<double>(airtimeMs) * 100.0);
  const uint32_t periodMs = static_cast<uint32_t>(
      std::min<double>(period, static_cast<double>(0x7fffffffu)));
  dutyCycleUntil = millis() + std::max<uint32_t>(periodMs, 1u);
}

void MAC::setFrequencyAndListen(uint16_t newChannel)
{
  if (!validChannel(newChannel))
    return;
  if (getMode() == SLEEPING)
    setMode(IDLE, true);
  channel = newChannel;
  calibratedFrequency = channels[channel];
  module.setFrequency(static_cast<float>(calibratedFrequency));
  setMode(RECEIVING, true);
}

void MAC::setFrequency(uint16_t newChannel)
{
  if (!validChannel(newChannel))
    return;
  if (getMode() == SLEEPING)
    setMode(IDLE, true);
  channel = newChannel;
  calibratedFrequency = channels[channel];
  module.setFrequency(static_cast<float>(calibratedFrequency));
}

int MAC::LORANoiseFloorCalibrate(int channelToMeasure, bool save)
{
  if (channelToMeasure < 0 || !validChannel(static_cast<uint16_t>(channelToMeasure)))
    return 255;

  const State previousState = getMode();
  const int previousChannel = channel;
  setFrequencyAndListen(static_cast<uint16_t>(channelToMeasure));

  int measurements[NUMBER_OF_MEASUREMENTS];
  for (int i = 0; i < NUMBER_OF_MEASUREMENTS; ++i)
  {
    measurements[i] = static_cast<int>(module.getRSSI(false));
    delay(TIME_BETWEENMEASUREMENTS);
  }
  MathExtension.quickSort(measurements, 0, NUMBER_OF_MEASUREMENTS - 1);

  int average = 0;
  for (int i = DISCRIMINATE_MEASURMENTS;
       i < NUMBER_OF_MEASUREMENTS - DISCRIMINATE_MEASURMENTS;
       ++i)
    average += measurements[i];
  average /= NUMBER_OF_MEASUREMENTS - DISCRIMINATE_MEASURMENTS * 2;

  // Store the raw measured floor. Squelch is applied exactly once by the
  // carrier-sense comparison below.
  if (save)
    noiseFloor[channelToMeasure] = average;

  if (previousChannel >= 0 && validChannel(static_cast<uint16_t>(previousChannel)))
    setFrequency(static_cast<uint16_t>(previousChannel));
  setMode(previousState, true);
  return average + squelch;
}

void MAC::LORANoiseCalibrateAllChannels(bool save)
{
  const State previousState = getMode();
  const int previousChannel = channel;
  for (uint8_t i = 0; i < channelCount; ++i)
    LORANoiseFloorCalibrate(i, save);
  if (previousChannel >= 0 && validChannel(static_cast<uint16_t>(previousChannel)))
    setFrequency(static_cast<uint16_t>(previousChannel));
  setMode(previousState, true);
}

MACPacket *MAC::createPacket(
    uint16_t sender,
    uint16_t target,
    unsigned char *data,
    uint8_t size)
{
  MACPacket *packet = static_cast<MACPacket *>(malloc(sizeof(MACHeader) + size));
  if (!packet)
    return nullptr;

  packet->sender = sender;
  packet->target = target;
  packet->crc32 = 0;
  if (size > 0)
    memcpy(packet->data, data, size);
  packet->crc32 = MathExtension.crc32c(0, packet->data, size);
  return packet;
}

void MAC::handlePacket()
{
  const uint16_t length = static_cast<uint16_t>(module.getPacketLength(true));
  if (length < sizeof(MACHeader))
    return;

  uint8_t *data = static_cast<uint8_t *>(malloc(length));
  if (!data)
    return;

  const int readStatus = module.readData(data, length);
  if (readStatus != RADIOLIB_ERR_NONE)
  {
    free(data);
    return;
  }

  MACPacket *packet = reinterpret_cast<MACPacket *>(data);
  const uint32_t crcReceived = packet->crc32;
  packet->crc32 = 0;
  const uint32_t crcCalculated = MathExtension.crc32c(
      0,
      packet->data,
      static_cast<uint32_t>(length - sizeof(MACHeader)));
  packet->crc32 = crcReceived;

  if ((packet->target == BROADCAST || packet->target == id) && RXCallback)
  {
    RXCallback(packet, length, crcCalculated);
    return; // ownership transfers to upper layer
  }
  if (packet->target != BROADCAST && packet->target != id && RXAlienCallback)
  {
    RXAlienCallback(packet, length, crcCalculated);
    return; // ownership transfers to callback
  }

  free(packet);
}

bool MAC::transmissionAuthorized()
{
  if (channel < 0 || !validChannel(static_cast<uint16_t>(channel)))
    return false;

  const State previousMode = getMode();

  // LoRa CAD is important because valid LoRa signals can be decodable below an
  // instantaneous RSSI/noise-floor threshold. RadioLib's default scanChannel()
  // settings are based on Semtech AN1200.48. Keep an energy test afterwards to
  // catch non-LoRa interferers that CAD intentionally ignores.
  setMode(IDLE, true);
  const int cad = module.scanChannel();
  if (cad == RADIOLIB_LORA_DETECTED || cad == RADIOLIB_PREAMBLE_DETECTED)
  {
    setMode(previousMode, true);
    return false;
  }

  setMode(RECEIVING, true);
  delay(TIME_BETWEENMEASUREMENTS / 3);
  int rssi = static_cast<int>(module.getRSSI(false));
  for (int i = 1; i < NUMBER_OF_MEASUREMENTS_LBT; ++i)
  {
    delay(TIME_BETWEENMEASUREMENTS);
    rssi += static_cast<int>(module.getRSSI(false));
  }
  rssi /= NUMBER_OF_MEASUREMENTS_LBT;

  setMode(previousMode, true);

  // If CAD returned an unexpected radio error, fall back to the energy check
  // rather than declaring the channel permanently unusable. Configuration and
  // TX errors are handled separately by sendData().
  return rssi < noiseFloor[channel] + squelch;
}

bool MAC::waitForTransmissionAuthorization(uint32_t timeout)
{
  (void)timeout; // retained for source compatibility; v2 never blocks here.
  const uint32_t now = millis();
  if (deadlinePending(now, carrierBackoffUntil))
    return false;

  if (transmissionAuthorized())
  {
    carrierBackoffUntil = now;
    return true;
  }

  startCarrierBackoff();
  return false;
}

void MAC::calibrateBasedOnLastPacket()
{
  // Deliberately disabled in protocol-v2. SX126x frequency-error reporting is
  // not a safe basis for cumulatively changing the local TX frequency from the
  // last peer's packet. A future correction loop must be bounded, filtered and
  // associated with a specific peer/channel.
}

uint8_t MAC::sendData(
    uint16_t target,
    unsigned char *data,
    uint8_t size,
    uint32_t timeout)
{
  if (getMode() == SENDING)
    return MAC_SEND_BUSY;
  if (channel < 0 || !validChannel(static_cast<uint16_t>(channel)))
    return MAC_SEND_RADIO_ERROR;
  if (size > DATASIZE_MAC)
    return MAC_SEND_TOO_LARGE;

  const uint32_t now = millis();
  if (region == MACRegion::EU868 && deadlinePending(now, dutyCycleUntil))
    return MAC_SEND_DUTY_CYCLE;

  if (!waitForTransmissionAuthorization(timeout))
    return MAC_SEND_CHANNEL_BUSY_TIMEOUT;

  MACPacket *packet = createPacket(id, target, data, size);
  if (!packet)
    return MAC_SEND_ALLOC_FAILED;

  const uint8_t finalPacketLength = static_cast<uint8_t>(MAC_OVERHEAD + size);
  operationDone = false;
  setMode(SENDING, true);
  const int result = module.startTransmit(
      reinterpret_cast<unsigned char *>(packet),
      finalPacketLength);
  free(packet);

  if (result != RADIOLIB_ERR_NONE)
  {
    // Do not wedge in SENDING waiting for an IRQ that will never arrive.
    setMode(RECEIVING, true);
    return MAC_SEND_RADIO_ERROR;
  }

  accountDutyCycle(finalPacketLength);
  return MAC_SEND_OK;
}

void MAC::loop()
{
  if (!operationDone)
    return;

  operationDone = false;
  if (getMode() == RECEIVING)
  {
    handlePacket();
    return;
  }

  if (getMode() == SENDING)
  {
    module.finishTransmit();
    setMode(RECEIVING, true);
    // Callback observes a radio that is already ready to receive. This avoids
    // the old illegal intermediate state where upper layers ran while SENDING.
    if (transmitDone)
      transmitDone();
  }
}

RAM_ATTR void MAC::setFlag(void)
{
  MAC::operationDone = true;
}

State MAC::getMode()
{
  return state;
}

void MAC::setMode(State newState, bool force)
{
  if (!force && state == newState)
    return;

  state = newState;
  switch (newState)
  {
  case SENDING:
  case IDLE:
    module.standby();
    break;
  case RECEIVING:
    module.startReceive();
    break;
  case SLEEPING:
    module.sleep(true);
    break;
  default:
    break;
  }
}
