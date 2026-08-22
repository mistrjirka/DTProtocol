#ifndef _GEREALSETTINGS_H_
#define _GEREALSETTINGS_H_
#define BROADCAST 0
#define MAX_PACKET_SIZE (255)
#define MAC_OVERHEAD (4+2+2)
#define LCMM_OVERHEAD (1+2)
#define DTP_OVERHEAD (1+2+2+2+2+1+1)

// Multipart application messages are bounded so a malformed or hostile peer
// cannot consume all MCU RAM. Applications may override these before including
// DTProtocol headers. The wire format itself supports up to 255 fragments.
#ifndef DTPK_MAX_MESSAGE_SIZE
#define DTPK_MAX_MESSAGE_SIZE (16u * 1024u)
#endif
#ifndef DTPK_MAX_FRAGMENT_ASSEMBLIES
#define DTPK_MAX_FRAGMENT_ASSEMBLIES 2u
#endif
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
