#include <BluetoothProtocol.h>
#include <DTPKDefinitions.h>

#include <array>
#include <cassert>
#include <cstring>

int main()
{
    static_assert(static_cast<uint8_t>(CRYST) == 0x30u);
    static_assert(static_cast<uint8_t>(SEQ_REQ) == 0x36u);
    static_assert(maxBLEInboundPayloadPerNotification() == 237u);
    static_assert(maxBLEInboundFragmentPayload() == 233u);
    assert(dtpkWireVersionSupported(static_cast<uint8_t>(DATA_SINGLE)));
    assert(!dtpkWireVersionSupported(0x01u));
    assert(!dtpkWireVersionSupported(0x41u));

    std::array<uint8_t, sizeof(BLEOutboundMessage) + 3> bytes{};
    BLEOutboundMessage *message =
        reinterpret_cast<BLEOutboundMessage *>(bytes.data());
    message->header.type = BLE_MSG_TYPE_OUTBOUND;
    message->header.messageId = 42;
    message->header.length = bytes.size();
    message->recipientId = 7;
    message->data[0] = 'a';
    message->data[1] = 'b';
    message->data[2] = 'c';

    BLEOutboundView view;
    assert(parseBLEOutboundMessage(bytes.data(), bytes.size(), 16, view));
    assert(view.header.messageId == 42);
    assert(view.recipientId == 7);
    assert(view.payloadSize == 3);
    assert(std::memcmp(view.payload, "abc", 3) == 0);

    message->header.length = static_cast<uint16_t>(bytes.size() - 1);
    assert(!parseBLEOutboundMessage(bytes.data(), bytes.size(), 16, view));
    message->header.length = bytes.size();
    message->recipientId = 0;
    assert(!parseBLEOutboundMessage(bytes.data(), bytes.size(), 16, view));
    message->recipientId = 7;
    assert(!parseBLEOutboundMessage(bytes.data(), bytes.size(), 2, view));
    return 0;
}
