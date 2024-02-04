#ifndef _GEREALSETTINGS_H_
#define _GEREALSETTINGS_H_
#define BROADCAST 0
#define MAX_PACKET_SIZE (255)
#define MAC_OVERHEAD (4+2+2)
#define LCMM_OVERHEAD (1+2)
#define DTP_OVERHEAD (2+2+2+1+1)
#define DATASIZE_MAC (MAX_PACKET_SIZE - MAC_OVERHEAD)
#define DATASIZE_LCMM (MAX_PACKET_SIZE - LCMM_OVERHEAD - MAC_OVERHEAD)
#define DATASIZE_DTP (MAX_PACKET_SIZE - DTP_OVERHEAD - LCMM_OVERHEAD - MAC_OVERHEAD)
#define BYTE_TO_BINARY_PATTERN "%c%c%c%c%c%c%c%c"
#define BYTE_TO_BINARY(byte)  \
  ((byte) & 0x80 ? '1' : '0'), \
  ((byte) & 0x40 ? '1' : '0'), \
  ((byte) & 0x20 ? '1' : '0'), \
  ((byte) & 0x10 ? '1' : '0'), \
  ((byte) & 0x08 ? '1' : '0'), \
  ((byte) & 0x04 ? '1' : '0'), \
  ((byte) & 0x02 ? '1' : '0'), \
  ((byte) & 0x01 ? '1' : '0') 

#endif // _GEREALSETTINGS_H_
