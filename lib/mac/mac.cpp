#include "include/mac.h"

State MAC::state = RECEIVING;
MAC *MAC::mac = nullptr;
bool MAC::transmission_detected = false;
bool somethingFalse = false;
/*
 * LORANoiseFloorCalibrate function calibrates noise floor of the LoRa channel
 * and returns the average value of noise measurements. The noise floor is used
 * to determine the sensitivity threshold of the receiver, i.e., the minimum
 * signal strength required for the receiver to detect a LoRa message. The
 * function takes two arguments: the channel number and a boolean flag
 * indicating whether to save the calibrated noise floor value to the
 * noise_floor_per_channel array. By default, save is true. The function returns
 * an integer value representing the average noise measurement.
 */
int MAC::LORANoiseFloorCalibrate(int channel, bool save /* = true */)
{
  State prev_state = getMode();
  this->setFrequencyAndListen(channel);
  // Set frequency to the given channel
  MAC::channel = channel;                         // Set current channel
  int noise_measurements[NUMBER_OF_MEASUREMENTS]; // Array to hold noise
  // measurements

  // Take NUMBER_OF_MEASUREMENTS noise measurements and store in
  // noise_measurements array

  for (int i = 0; i < NUMBER_OF_MEASUREMENTS; i++)
  {
    noise_measurements[i] = this->module.getRSSI(false);
    delay(TIME_BETWEENMEASUREMENTS);
    // this->module.nonBlockingDelay(TIME_BETWEENMEASUREMENTS);
  }

  // Sort the array in ascending order using quickSort algorithm
  MathExtension.quickSort(noise_measurements, 0, NUMBER_OF_MEASUREMENTS - 1);

  // Calculate the average noise measurement by excluding the
  // DISCRIMINATE_MEASURMENTS highest values
  int average = 0;
  for (int i = DISCRIMINATE_MEASURMENTS; i < (NUMBER_OF_MEASUREMENTS - DISCRIMINATE_MEASURMENTS);
       i++)
  {
    average += noise_measurements[i];
  }
  average = average / (NUMBER_OF_MEASUREMENTS - DISCRIMINATE_MEASURMENTS * 2);

  // If save is true, save the calibrated noise floor value to
  // noise_floor_per_channel array
  if (save)
  {
    noiseFloor[channel] = (int)(average + squelch);
  }

  return (
      int)(average +
           squelch); // Return the average noise measurement plus squelch value
  setMode(prev_state);
}

void MAC::setFrequencyAndListen(uint16_t channel)
{
  if (getMode() == SLEEPING)
    setMode(IDLE);
  setMode(RECEIVING);
  this->module.setFrequency(channels[channel]); // Set frequency to the given channel
  this->module.startReceive();
}

void MAC::LORANoiseCalibrateAllChannels(bool save /*= true*/)
{
  State prev_state = getMode();

  int previusChannel = MAC::channel;
  // Calibrate noise floor for all channels and save if requested
  for (int i = 0; i < NUM_OF_CHANNELS; i++)
  {
    this->LORANoiseFloorCalibrate(i, save);
  }
  MAC::channel = previusChannel;
  // Set LoRa to idle and set frequency to current channel
  this->module.setFrequency(channels[previusChannel]);
  setMode(prev_state);
}

RAM_ATTR void MAC::RecievedPacket()
{

  MAC::getInstance()->readyToReceive = true;
}

void MAC::handlePacket()
{
  Serial.println("packet received");
  /*String packetString;
  int state = this->module.readData(packetString);
  if (state != RADIOLIB_ERR_NONE)
  {
    Serial.print("Error during recieve \n");
    return;
  }

  uint16_t size = packetString.length();
  const char *packetBytes = packetString.c_str();

  MACPacket *packet = (MACPacket *)packetBytes;

  uint32_t crcRecieved = packet->crc32;
  packet->crc32 = 0;
  uint32_t crcCalculated =
      MathExtension.crc32c(0, packet->data, size - sizeof(MACPacket));
  packet->crc32 = crcRecieved;

  RXCallback(packet, size - sizeof(MACPacket), crcCalculated);*/
}

void MAC::loop()
{
  if(somethingFalse){
    somethingFalse = false;
    Serial.println("fallllllse");
  }
  if (readyToReceive)
    handlePacket();
}

static void transmitInterrupt(){
  somethingFalse = true;
}

MAC::MAC(
    SX1262 loramodule,
    int id,
    int default_channel /* = DEFAULT_CHANNEL*/,
    int default_spreading_factor /* = DEFAULT_SPREADING_FACTOR*/,
    float default_bandwidth /* = DEFAULT_SPREADING_FACTOR*/,
    int squelch /*= DEFAULT_SQUELCH*/,
    int default_power /* = DEFAULT_POWER*/,
    int default_coding_rate /*DEFAULT_CODING_RATE*/
    ) : module(loramodule)
{
  this->readyToReceive = false;
  this->id = id;
  this->channel = default_channel;
  this->spreading_factor = default_spreading_factor;
  this->bandwidth = default_bandwidth;
  this->squelch = squelch;
  this->power = default_power;
  this->coding_rate = default_coding_rate;

  // Initialize the LoRa module with the specified settings
  this->module.setOutputPower(default_power);
  this->module.setSpreadingFactor(default_spreading_factor);
  this->module.setBandwidth(default_bandwidth);
  this->module.setCodingRate(default_coding_rate);
  this->module.setSyncWord(DEFAULT_SYNC_WORD);
  this->module.setPreambleLength(DEFAULT_PREAMBLE_LENGTH);

  this->module.setPacketReceivedAction(MAC::RecievedPacket);
  this->module.setPacketSentAction(transmitInterrupt);
  LORANoiseCalibrateAllChannels(true);
  setMode(RECEIVING, true);

}

void MAC::initialize(
    SX1262 loramodule,
    int id, int default_channel /* = DEFAULT_CHANNEL*/,
    int default_spreading_factor /* = DEFAULT_SPREADING_FACTOR*/,
    float default_bandwidth /* = DEFAULT_SPREADING_FACTOR*/,
    int squelch /*= DEFAULT_SQUELCH*/, int default_power /* = DEFAULT_POWER*/,
    int default_coding_rate /*DEFAULT_CODING_RATE*/)
{
  if (mac == nullptr)
  {
    mac =
        new MAC(loramodule, id, default_channel, default_spreading_factor,
                default_bandwidth, squelch, default_power, default_coding_rate);
  }
}

MAC::~MAC()
{
  // Destructor implementation if needed
}

int MAC::getNoiseFloorOfChannel(uint8_t channel)
{
  if (channel > NUM_OF_CHANNELS)
    return 255;

  return this->noiseFloor[channel];
}

uint8_t MAC::getNumberOfChannels()
{
  return NUM_OF_CHANNELS;
}



/**
 * Creates a MAC packet with the given data.
 * @param sender The sender node ID.
 * @param target The target node ID.
 * @param data The data to include in the packet.
 * @param size The size of the data in bytes.
 * @return The created MAC packet.
 */

MACPacket *MAC::createPacket(uint16_t sender, uint16_t target,
                             unsigned char *data, uint8_t size)
{
  MACPacket *packet = (MACPacket *)malloc(sizeof(MACPacket) + size);
  if (!packet)
  {
    return NULL;
  }
  (*packet).sender = sender;
  (*packet).target = target;
  (*packet).crc32 = 0;
  memcpy((*packet).data, data, size);

  (*packet).crc32 = MathExtension.crc32c(0, data, size);

  return packet;
}

/**
 * Sends data to a target node.
 * @param sender The sender node ID.
 * @param target The target node ID.
 * @param data The data to send.
 * @param size The size of the data in bytes.
 * @throws std::invalid_argument if the data size is greater than the maximum
 * allowed size.
 */

uint8_t MAC::sendData(uint16_t target, unsigned char *data, uint8_t size,
                      bool nonblocking, uint32_t timeout /*= 5000*/)
{
  State previousMode = getMode();

  if (size > DATASIZE_MAC)
  {
    Serial.println("Data size cannot be greater than 247 bytes\n");
    return 3;
  }

  MACPacket *packet = createPacket(this->id, target, data, size);
  if (!packet)
  {
    return 2;
  }

  uint8_t finalPacketLength = MAC_OVERHEAD + size;
  unsigned char *packetBytes = (unsigned char *)packet;

  /*if (!waitForTransmissionAuthorization(timeout))
  {
    printf("timeout\n");
    free(packetBytes);
    return 1;
  }*/
  Serial.print("starting to send->");

  setMode(IDLE);

  this->module.transmit(packetBytes, finalPacketLength);

  Serial.println("finished");

  free(packetBytes);
  setMode(previousMode, true);
  return 0;
}

/**
 * Waits for transmission authorization for a given timeout period.
 * @param timeout The timeout period in milliseconds.
 * @return true if transmission is authorized within the timeout period, false
 * otherwise.
 */

bool MAC::waitForTransmissionAuthorization(uint32_t timeout)
{
  uint32_t start = millis() / 1000;
  while (millis() / 1000 - start < timeout && !transmissionAuthorized())
  {
    delay(TIME_BETWEENMEASUREMENTS);
  }
  return millis() / 1000 - start < timeout;
}

bool MAC::transmissionAuthorized()
{
  State previousMode = getMode();
  setMode(RECEIVING);
  delay(TIME_BETWEENMEASUREMENTS / 3);
  int rssi = this->module.getRSSI(false);

  for (int i = 1; i < NUMBER_OF_MEASUREMENTS_LBT; i++)
  {
    delay(TIME_BETWEENMEASUREMENTS);
    rssi += this->module.getRSSI(false);
  }
  rssi /= NUMBER_OF_MEASUREMENTS_LBT;
  Serial.println("RSSI: " + String(rssi) + "noise floor" + String(noiseFloor[channel] + squelch));
  //Serial.printf("rssi %d roof %d \n ", rssi, noiseFloor[channel] + squelch);

  setMode(previousMode);
  return rssi < noiseFloor[channel] + squelch;
}

MAC *MAC::getInstance()
{
  if (mac == nullptr)
  {
    // Throw an exception or handle the error case if initialize() has not been
    // called before getInstance()
    return nullptr;
  }
  return mac;
}

State MAC::getMode() { return state; }

void MAC::setMode(State state, boolean force)
{
  if (force || MAC::getMode() != state)
  {
    MAC::state = state;
    switch (state)
    {
    case IDLE:
      this->module.standby();
      break;
    case RECEIVING:
      this->module.startReceive();
      break;
    case SLEEPING:
      this->module.sleep(true);
      break;
    default:
      break;
    }
  }
}


void MAC::setRXCallback(PacketReceivedCallback callback)
{
  RXCallback = callback;
}