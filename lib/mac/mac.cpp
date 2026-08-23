#include "include/mac.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

State MAC::state = RECEIVING;
MAC *MAC::mac = nullptr;
volatile bool MAC::operationDone = false;

const double MAC::EU433_CHANNELS[] = {
    433.175, 433.300, 433.425, 433.550, 433.675, 433.800, 433.925,
    434.050, 434.175, 434.300, 434.425, 434.550, 434.675};
const double MAC::EU868_CHANNELS[] = {868.100, 868.300, 868.500};
const double MAC::EU869_HIGH_DUTY_CHANNELS[] = {869.525};
const uint8_t MAC::EU433_CHANNEL_COUNT = 13;
const uint8_t MAC::EU868_CHANNEL_COUNT = 3;
const uint8_t MAC::EU869_HIGH_DUTY_CHANNEL_COUNT = 1;


bool MAC::irqPending()
{
  noInterrupts();
  const bool pending = operationDone;
  interrupts();
  return pending;
}

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
      channels(selectedRegion == MACRegion::EU868
                   ? EU868_CHANNELS
                   : (selectedRegion == MACRegion::EU869_HIGH_DUTY
                          ? EU869_HIGH_DUTY_CHANNELS
                          : EU433_CHANNELS)),
      channelCount(selectedRegion == MACRegion::EU868
                       ? EU868_CHANNEL_COUNT
                       : (selectedRegion == MACRegion::EU869_HIGH_DUTY
                              ? EU869_HIGH_DUTY_CHANNEL_COUNT
                              : EU433_CHANNEL_COUNT)),
      region(selectedRegion),
      maxConductedPowerDbm(selectedRegion == MACRegion::EU869_HIGH_DUTY
                               ? 20
                               : (selectedRegion == MACRegion::EU868 ? 13 : 10)),
      // Region selection does not automatically impose duty throttling. The
      // strict legal fallback limits are available through
      // setDutyCycleLimitPercent() when an application explicitly wants them.
      dutyCyclePercent(0),
      cadCarrierSenseEnabled(false),
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
      dutyCycleUntil(0),
      ready(false),
      lastRadioError(RADIOLIB_ERR_NONE)
{
  memset(noiseFloor, 0, sizeof(noiseFloor));

  if (!validChannel(static_cast<uint16_t>(channel)) ||
      !configureRadio(spreading_factor, bandwidth, power, coding_rate))
  {
    channel = -1;
    state = IDLE;
    return;
  }

  module.setDio1Action(setFlag);
  LORANoiseCalibrateAllChannels(true);
  if (!setFrequency(static_cast<uint16_t>(channel)) ||
      !setMode(RECEIVING, true))
  {
    channel = -1;
    state = IDLE;
    return;
  }
  ready = true;
}

MAC::~MAC() {}

bool MAC::initialize(
    SX1262 &loramodule,
    int id,
    int default_channel,
    int default_spreading_factor,
    float default_bandwidth,
    int squelch,
    int default_power,
    int default_coding_rate)
{
  return initialize(
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

bool MAC::initialize(
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
  return mac != nullptr && mac->isReady();
}

MAC *MAC::getInstance()
{
  return mac;
}

bool MAC::validChannel(uint16_t candidate) const
{
  return candidate < channelCount;
}

bool MAC::recordRadioStatus(int status, const char *operation)
{
  if (status == RADIOLIB_ERR_NONE)
    return true;
  lastRadioError = static_cast<int16_t>(status);
  ++diagnostics.radioCommandErrors;
  Serial.print("[MAC] RadioLib error ");
  Serial.print(status);
  Serial.print(" during ");
  Serial.println(operation ? operation : "unknown");
  return false;
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
  ok = recordRadioStatus(module.setFrequency(static_cast<float>(calibratedFrequency)), "setFrequency") && ok;
  ok = recordRadioStatus(module.setOutputPower(defaultPower), "setOutputPower") && ok;
  ok = recordRadioStatus(module.setBandwidth(defaultBandwidth), "setBandwidth") && ok;
  ok = recordRadioStatus(module.setSpreadingFactor(defaultSpreadingFactor), "setSpreadingFactor") && ok;
  ok = recordRadioStatus(module.setCodingRate(defaultCodingRate), "setCodingRate") && ok;
  ok = recordRadioStatus(module.setSyncWord(DEFAULT_SYNC_WORD), "setSyncWord") && ok;
  ok = recordRadioStatus(module.setPreambleLength(DEFAULT_PREAMBLE_LENGTH), "setPreambleLength") && ok;
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
  if (dutyCyclePercent > 0 && deadlinePending(now, dutyCycleUntil))
    wait = std::max(wait, dutyCycleUntil - now);
  return wait;
}

void MAC::startCarrierBackoff()
{
  const uint32_t delayMs = 25u + module.random(226u);
  carrierBackoffUntil = millis() + delayMs;
}

void MAC::accountDutyCycle(uint8_t packetLength)
{
  if (dutyCyclePercent == 0)
    return;

  const float airtimeMs = MathExtension.timeOnAir(
      packetLength,
      DEFAULT_PREAMBLE_LENGTH,
      static_cast<uint8_t>(spreading_factor),
      bandwidth,
      static_cast<uint8_t>(coding_rate));

  if (!(airtimeMs > 0.0f) || !std::isfinite(airtimeMs))
    return;

  const double multiplier = 100.0 / static_cast<double>(dutyCyclePercent);
  const double period = std::ceil(static_cast<double>(airtimeMs) * multiplier);
  const uint32_t periodMs = static_cast<uint32_t>(
      std::min<double>(period, static_cast<double>(0x7fffffffu)));
  dutyCycleUntil = millis() + std::max<uint32_t>(periodMs, 1u);
}

bool MAC::setFrequencyAndListen(uint16_t newChannel)
{
  if (!validChannel(newChannel))
    return false;
  if (getMode() == SLEEPING && !setMode(IDLE, true))
    return false;
  channel = newChannel;
  calibratedFrequency = channels[channel];
  if (!recordRadioStatus(
          module.setFrequency(static_cast<float>(calibratedFrequency)),
          "setFrequency"))
    return false;
  return setMode(RECEIVING, true);
}

bool MAC::setFrequency(uint16_t newChannel)
{
  if (!validChannel(newChannel))
    return false;
  if (getMode() == SLEEPING && !setMode(IDLE, true))
    return false;
  channel = newChannel;
  calibratedFrequency = channels[channel];
  return recordRadioStatus(
      module.setFrequency(static_cast<float>(calibratedFrequency)),
      "setFrequency");
}

int MAC::LORANoiseFloorCalibrate(int channelToMeasure, bool save)
{
  if (channelToMeasure < 0 || !validChannel(static_cast<uint16_t>(channelToMeasure)))
    return 255;

  const State previousState = getMode();
  const int previousChannel = channel;
  if (!setFrequencyAndListen(static_cast<uint16_t>(channelToMeasure)))
    return 255;

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
  {
    ++diagnostics.rxTooShort;
    return;
  }

  uint8_t *data = static_cast<uint8_t *>(malloc(length));
  if (!data)
  {
    ++diagnostics.rxAllocationFailures;
    return;
  }

  const int readStatus = module.readData(data, length);
  if (readStatus != RADIOLIB_ERR_NONE)
  {
    ++diagnostics.rxReadErrors;
    recordRadioStatus(static_cast<int16_t>(readStatus), "readData");
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
    return;
  }
  if (packet->target != BROADCAST && packet->target != id && RXAlienCallback)
  {
    RXAlienCallback(packet, length, crcCalculated);
    return;
  }

  free(packet);
}

bool MAC::transmissionAuthorized()
{
  if (channel < 0 || !validChannel(static_cast<uint16_t>(channel)))
    return false;

  // RSSI sensing is deliberately done while continuous RX remains armed. The
  // previous CAD-first path repeatedly moved RX -> standby/CAD -> RX and could
  // destroy a packet that began during carrier sensing.
  if (getMode() != RECEIVING)
    setMode(RECEIVING, true);

  if (irqPending())
    return false;

  int rssi = 0;
  for (int i = 0; i < NUMBER_OF_MEASUREMENTS_LBT; ++i)
  {
    if (i != 0)
      delay(TIME_BETWEENMEASUREMENTS);
    if (irqPending())
      return false;
    rssi += static_cast<int>(module.getRSSI(false));
  }
  rssi /= NUMBER_OF_MEASUREMENTS_LBT;

  if (irqPending() || rssi >= noiseFloor[channel] + squelch)
    return false;

  if (!cadCarrierSenseEnabled)
    return true;

  // Optional CAD catches matching LoRa below the RSSI threshold, but is not a
  // reliable general carrier detector. scanChannel() is synchronous in
  // RadioLib and temporarily replaces RX/CAD IRQ state, so only run it after
  // RSSI is clear and after confirming no RX IRQ is pending.
  const int cad = module.scanChannel();

  // A synchronous CAD completion can trigger our shared DIO1 callback. It
  // cannot be RX_DONE while the chip is in CAD, so consume only that wake flag.
  noInterrupts();
  operationDone = false;
  interrupts();

  if (cad != RADIOLIB_CHANNEL_FREE)
  {
    // Detection and errors both fail closed. Re-arm continuous RX; RadioLib's
    // startReceive path also restores RX IRQ mapping and clears stale CAD IRQs.
    setMode(RECEIVING, true);
    return false;
  }

  return true;
}

bool MAC::waitForTransmissionAuthorization(uint32_t timeout)
{
  (void)timeout;
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
}

uint8_t MAC::sendData(
    uint16_t target,
    unsigned char *data,
    uint8_t size,
    uint32_t timeout)
{
  if (!ready)
    return MAC_SEND_RADIO_ERROR;
  if (getMode() == SENDING)
    return MAC_SEND_BUSY;
  if (channel < 0 || !validChannel(static_cast<uint16_t>(channel)))
    return MAC_SEND_RADIO_ERROR;
  if (size > DATASIZE_MAC)
    return MAC_SEND_TOO_LARGE;

  const uint32_t now = millis();
  if (dutyCyclePercent > 0 && deadlinePending(now, dutyCycleUntil))
    return MAC_SEND_DUTY_CYCLE;

  if (!waitForTransmissionAuthorization(timeout))
    return MAC_SEND_CHANNEL_BUSY_TIMEOUT;

  MACPacket *packet = createPacket(id, target, data, size);
  if (!packet)
    return MAC_SEND_ALLOC_FAILED;

  // Do not blindly clear operationDone here: an RX_DONE may have arrived after
  // the final CCA sample. Preserve it and defer this transmission instead.
  noInterrupts();
  const bool receivePending = operationDone;
  if (!receivePending)
    operationDone = false;
  interrupts();
  if (receivePending)
  {
    free(packet);
    startCarrierBackoff();
    return MAC_SEND_CHANNEL_BUSY_TIMEOUT;
  }

  const uint8_t finalPacketLength = static_cast<uint8_t>(MAC_OVERHEAD + size);
  if (!setMode(SENDING, true))
  {
    free(packet);
    return MAC_SEND_RADIO_ERROR;
  }
  const int result = module.startTransmit(
      reinterpret_cast<unsigned char *>(packet),
      finalPacketLength);
  free(packet);

  if (result != RADIOLIB_ERR_NONE)
  {
    recordRadioStatus(static_cast<int16_t>(result), "startTransmit");
    setMode(RECEIVING, true);
    return MAC_SEND_RADIO_ERROR;
  }

  accountDutyCycle(finalPacketLength);
  return MAC_SEND_OK;
}

void MAC::loop()
{
  noInterrupts();
  const bool pending = operationDone;
  operationDone = false;
  interrupts();

  if (!pending)
    return;

  // RadioLib 6.x exposes getIrqStatus() publicly. Newer getIrqFlags() helpers
  // must not be required while library.json still declares RadioLib ^6.0.0.
  const uint16_t irq = module.getIrqStatus();

  if ((irq & RADIOLIB_SX126X_IRQ_TX_DONE) != 0)
  {
    module.finishTransmit();
    setMode(RECEIVING, true);
    if (transmitDone)
      transmitDone();
    return;
  }

  if ((irq & RADIOLIB_SX126X_IRQ_RX_DONE) != 0)
  {
    handlePacket();
    if (getMode() != SENDING)
      setMode(RECEIVING, true);
    return;
  }

  // clearIrqStatus() is protected in RadioLib 6.x. Re-entering receive through
  // the public API restores RX IRQ mapping and clears stale CAD/error flags.
  // Do not infer event type from mutable software state.
  if (getMode() != SENDING)
    setMode(RECEIVING, true);
}

RAM_ATTR void MAC::setFlag(void)
{
  MAC::operationDone = true;
}

State MAC::getMode()
{
  return state;
}

bool MAC::setMode(State newState, bool force)
{
  if (!force && state == newState)
    return true;

  int status = RADIOLIB_ERR_NONE;
  switch (newState)
  {
  case SENDING:
  case IDLE:
    status = module.standby();
    break;
  case RECEIVING:
    status = module.startReceive();
    break;
  case SLEEPING:
    status = module.sleep(true);
    break;
  default:
    return false;
  }

  if (!recordRadioStatus(status,
          newState == RECEIVING ? "startReceive" :
          (newState == SLEEPING ? "sleep" : "standby")))
    return false;
  state = newState;
  return true;
}
