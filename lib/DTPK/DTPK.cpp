#include "mathextension.h"
#include <DTPK.h>

DTPK *DTPK::dtpk = nullptr;

void DTPK::initialize(uint8_t KLimit)
{
    if (!dtpk)
        dtpk = new DTPK(KLimit);
}

DTPK *DTPK::getInstance()
{
    if (!dtpk)
    {
        return nullptr;
    }
    return dtpk;
}



DTPK::DTPK(uint8_t Klimit)
{
    this->Klimit = Klimit;
    this->seed = MathExtension.murmur64(MAC::getInstance()->random()
                                            << 32 |
                                        MAC::getInstance()->random());

    randomSeed(this->seed);

    LCMM::initialize(DTPK::receivePacket, DTPK::receiveAck);
    
    MAC::getInstance()->setMode(RECEIVING, true);
}

void DTPK::loop()
{
    
}

DTPKPacketCryst *prepeareCrystPacket()
{
    
}

void DTPK::receivePacket(LCMMPacketDataRecieve *packet, uint16_t size) {
  /*if(packet->mac.sender == 5){// only for testing purposes
      free(packet);
      return;
  }*/

  Serial.println("Packet received");
  DTPKPacketUnkown *dtpkPacket = (DTPKPacketUnkown *)packet->data;

  Serial.println(dtpkPacket->type);
  uint16_t dtpSize = size - sizeof(LCMMPacketDataRecieve);
  printf("Packet received %d\n", dtpkPacket->type);
  switch (dtpkPacket->type) {
  case CRYST:
    Serial.println("NAP packet received");
    break;
  case DATA_SINGLE:
    Serial.println("DATA packet received");
    break;
  case ACK:
    Serial.println("ACK packet received");
    break;
  case NACK_NOTFOUND:
    Serial.println("NACK packet received");
    break;
  }
}

void DTPK::receiveAck(uint16_t id, bool success) {
  printf("Ack received\n");
}