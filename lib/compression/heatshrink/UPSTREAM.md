# Vendored heatshrink

This directory contains codec sources from `atomicobject/heatshrink` tag
`v0.4.1` (`19c3834b62fd69869eede8ee7fbdff47f69c5193`). Source semantics are
unchanged; trailing whitespace was normalized and `heatshrink_config.h` carries
the local static-allocation configuration.
The upstream ISC license is in `LICENSE`.

DTProtocol uses static allocation, an 8-bit (256-byte) window, a 4-bit
lookahead, and the encoder search index. Every compressed candidate is decoded
and compared with its original bytes before it is allowed onto the wire.
