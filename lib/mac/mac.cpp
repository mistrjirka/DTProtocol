#include "mac.h"

State MAC::state = RECEIVING;
MAC *MAC::mac = nullptr;
bool MAC::transmission_detected = false;
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
int MAC::LORANoiseFloorCalibrate(int channel, bool save /* = true */
)
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
    noise_measurements[i] = watch.getRSSI(false);
    watch.nonBlockingDelay(TIME_BETWEENMEASUREMENTS);
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
  watch.setFrequency(channels[channel]); // Set frequency to the given channel
  watch.startReceive();
}

void MAC::LORANoiseCalibrateAllChannels(bool save /*= true*/
)
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
  watch.setFrequency(channels[previusChannel]);
  setMode(prev_state);
}

ICACHE_RAM_ATTR void MAC::RecievedPacket()
{
  Serial.println("packet received");
  MAC::getInstance()->readyToReceive = true;
}

void MAC::handlePacket()
{
  printf("handling packet\n");

  String packetString;
  int state = watch.readData(packetString);
  if (state != RADIOLIB_ERR_NONE)
  {
    Serial.printf("Error during recieve %d \n", state);
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

  RXCallback(packet, size - sizeof(MACPacket), crcCalculated);
}

void MAC::loop()
{
  if (readyToReceive)
    handlePacket();
}

MAC::MAC(int id,
         int default_channel /* = DEFAULT_CHANNEL*/,
         int default_spreading_factor /* = DEFAULT_SPREADING_FACTOR*/,
         int default_bandwidth /* = DEFAULT_SPREADING_FACTOR*/,
         int squelch /*= DEFAULT_SQUELCH*/,
         int default_power /* = DEFAULT_POWER*/,
         int default_coding_rate /*DEFAULT_CODING_RATE*/)
{
  this->id = id;
  this->channel = default_channel;
  this->spreading_factor = default_spreading_factor;
  this->bandwidth = default_bandwidth;
  this->squelch = squelch;
  this->power = default_power;
  this->coding_rate = default_coding_rate;

  // Initialize the LoRa module with the specified settings
  watch.setOutputPower(default_power);
  watch.setSpreadingFactor(default_spreading_factor);
  watch.setBandwidth(default_bandwidth);
  watch.setCodingRate(default_coding_rate);
  watch.setSyncWord(DEFAULT_SYNC_WORD);
  watch.setPreambleLength(DEFAULT_PREAMBLE_LENGTH);

  watch.setPacketReceivedAction(MAC::RecievedPacket);
  setMode(RECEIVING, true);
  LORANoiseCalibrateAllChannels(true);
  printf("channels calibrated\n");
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

void MAC::initialize(
    int id, int default_channel /* = DEFAULT_CHANNEL*/,
    int default_spreading_factor /* = DEFAULT_SPREADING_FACTOR*/,
    int default_bandwidth /* = DEFAULT_SPREADING_FACTOR*/,
    int squelch /*= DEFAULT_SQUELCH*/, int default_power /* = DEFAULT_POWER*/,
    int default_coding_rate /*DEFAULT_CODING_RATE*/)
{
  if (mac == nullptr)
  {
    mac =
        new MAC(id, default_channel, default_spreading_factor,
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
  
  
  watch.transmit(packetBytes, finalPacketLength);
  
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
/*
bool MAC::waitForTransmissionAuthorization(uint32_t timeout)
{
uint32_t start = time_us_32() / 1000;
while (time_us_32() / 1000 - start < timeout && !transmissionAuthorized())
{
 busy_wait_ms(30);
 tight_loop_contents();
}
return time_us_32() / 1000 - start < timeout;
}


bool MAC::transmissionAuthorized()
{
State previousMode = getMode();
setMode(RECEIVING);
delay(TIME_BETWEENMEASUREMENTS / 3);
int rssi = LoRa.rssi();

for (int i = 1; i < NUMBER_OF_MEASUREMENTS_LBT; i++)
{
 delay(TIME_BETWEENMEASUREMENTS);
 rssi += LoRa.rssi();
}
rssi /= NUMBER_OF_MEASUREMENTS_LBT;
printf("rssi %d roof %d \n ", rssi, noiseFloor[channel] + squelch);

setMode(previousMode);
return rssi < noiseFloor[channel] + squelch;
}  */
State MAC::getMode() { return state; }

void MAC::setMode(State state, boolean force)
{
  printf("setting the mode %d\n", state);
  if (force || MAC::getMode() != state)
  {
    MAC::state = state;
    switch (state)
    {
    case IDLE:
      watch.standby();
      break;
    case RECEIVING:
      watch.startReceive();
      break;
    case SLEEPING:
      watch.sleepLora(true);
      break;
    default:
      break;
    }
  }
}
/*
void MAC::setRXCallback(PacketReceivedCallback callback)
{
RXCallback = callback;
}*/