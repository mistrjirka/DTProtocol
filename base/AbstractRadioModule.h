#include <RadioLib.h>

#ifndef IRADIOMODULE_H
#define IRADIOMODULE_H

enum RModes { IDLE, SENDING, RECEIVING, SLEEPING };

class AbstractRadioModule {
    public:
        virtual uint8_t transmit(uint8_t *data, uint16_t dataLength);
        virtual void setMode(bool force);
        virtual RModes getMode();
        virtual void setReceiveInterrupt(void (*func)());
        virtual void setTransmitInterrupt(void (*func)());
        virtual void receiveBlocking(uint8_t *data, size_t &length);
        virtual void withDrawData(uint8_t *data, size_t &length);
        virtual void receive();
        virtual void setSpreadingFactor(uint8_t sf);
        virtual void setBandwidth(float bw);
        virtual void setCodingRate(uint8_t cr);
        virtual void setFrequecny(float fq);
        virtual void setPower(int power);
        virtual float getRSSI(bool lastPacket = false);
        virtual float getSNR();
        virtual float getFrequencyError();
};

#endif