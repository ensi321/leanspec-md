---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/node/networking/gossipsub/parameters.py
  - src/lean_spec/node/networking/gossipsub/topic.py
  - src/lean_spec/node/networking/gossipsub/behavior.py
  - src/lean_spec/node/networking/gossipsub/mesh.py
  - src/lean_spec/node/networking/gossipsub/mcache.py
  - src/lean_spec/node/networking/gossipsub/message.py
  - src/lean_spec/node/networking/gossipsub/rpc.py
  - src/lean_spec/node/networking/reqresp/codec.py
  - src/lean_spec/node/networking/reqresp/handler.py
  - src/lean_spec/node/networking/reqresp/message.py
  - src/lean_spec/node/networking/enr/enr.py
  - src/lean_spec/node/networking/enr/rlp.py
  - src/lean_spec/node/networking/enr/keys.py
  - src/lean_spec/node/networking/enr/eth2.py
  - src/lean_spec/node/networking/transport/peer_id.py
  - src/lean_spec/node/networking/transport/protocols.py
  - src/lean_spec/node/snappy/framing.py
  - src/lean_spec/node/snappy/encoding.py
  - src/lean_spec/node/snappy/compress.py
  - src/lean_spec/node/snappy/decompress.py
  - src/lean_spec/node/networking/varint.py
related_prs: []
---

# p2p Substrate

<!-- TOC -->

- [Introduction](#introduction)
- [Topic structure (gossipsub)](#topic-structure-gossipsub)
  - [Topic string format](#topic-string-format)
  - [Encoding](#encoding)
- [Gossipsub parameters](#gossipsub-parameters)
  - [Mesh degree (D)](#mesh-degree-d)
  - [Timing](#timing)
  - [Message cache](#message-cache)
- [Request / Response](#request--response)
  - [Wire format](#wire-format)
  - [Protocol ID format](#protocol-id-format)
  - [Response code](#response-code)
- [Snappy framing](#snappy-framing)
  - [Stream identifier](#stream-identifier)
  - [Chunk format](#chunk-format)
  - [Why framed and not raw](#why-framed-and-not-raw)
- [Varint encoding](#varint-encoding)
- [ENR records](#enr-records)
  - [Record structure](#record-structure)
  - [Identity scheme](#identity-scheme)
  - [Text encoding](#text-encoding)
- [Transport](#transport)

<!-- /TOC -->

## Introduction

The p2p substrate is the fork-independent machinery that every Lean client speaks on the wire.
It covers gossipsub mesh maintenance, request/response wire format, snappy framing, varint length prefixes, and ENR peer records.

Fork-specific concerns (topic IDs, fork digest, reqresp protocol IDs, message body schemas) live in `specs/<fork>/p2p-interface.md`.
This file defines the parts that do not change when a new fork ships.

The substrate is reference-equivalent to the analogous sections of `ethereum/consensus-specs` `phase0/p2p-interface.md`, with deviations called out explicitly.

## Topic structure (gossipsub)

### Topic string format

Topics follow the structured format:

```
/{prefix}/{network_name}/{topic_name}/{encoding}
```

| Component | Description |
| --- | --- |
| `prefix` | Network identifier; constant across all Lean fork topics. The current value is `leanconsensus` |
| `network_name` | 4-byte fork identifier as hex; equals the fork's `GOSSIP_DIGEST` |
| `topic_name` | Message type identifier; defined per fork |
| `encoding` | Serialization format; always `ssz_snappy` |

Example: `/leanconsensus/12345678/block/ssz_snappy`.

The `network_name` (fork digest) ensures peers on different forks do not exchange incompatible messages.
A topic carrying the wrong digest is rejected at parse time with `ForkMismatchError`.

### Encoding

The encoding suffix is always `ssz_snappy`.
A gossip payload is SSZ-encoded, then snappy-framed (see below).
There is no alternate encoding for any topic; clients reject any other suffix.

## Gossipsub parameters

The mesh and timing parameters follow the Ethereum consensus P2P specification.

### Mesh degree (D)

| Parameter | Default | Description |
| --- | --- | --- |
| `d` | 8 | Target mesh size per topic |
| `d_low` | 6 | Minimum mesh size before grafting |
| `d_high` | 12 | Maximum mesh size before pruning |
| `d_lazy` | 6 | Non-mesh peers reached via IHAVE gossip per heartbeat |

The heartbeat maintains the mesh toward `d`:

- If mesh size drops below `d_low`, graft peers up to `d`.
- If mesh size rises above `d_high`, prune peers down to `d`.

### Timing

| Parameter | Default | Description |
| --- | --- | --- |
| `heartbeat_interval_secs` | 0.7 | Frequency of mesh maintenance ticks |
| `fanout_ttl_secs` | 60 | Time-to-live for fanout peers used when publishing to non-subscribed topics |

### Message cache

| Parameter | Default | Description |
| --- | --- | --- |
| `mcache_len` | 6 | Total history windows kept |
| `mcache_gossip` | 3 | Most recent windows advertised via IHAVE |
| `seen_ttl_secs` | derived | Duplicate-detection window; default = `SECONDS_PER_SLOT * JUSTIFICATION_LOOKBACK_SLOTS * 2` |

The seen-id TTL is long enough to bound duplicate detection across realistic propagation delays and short enough to bound memory usage.

## Request / Response

### Wire format

ReqResp runs over libp2p streams.
The wire format is byte-streaming-friendly, snappy-compressed, and size-bounded.

Request payload:

```
[varint: uncompressed_length] [snappy_framed_ssz_payload]
```

Response payload:

```
[response_code: 1 byte] [varint: uncompressed_length] [snappy_framed_ssz_payload]
```

The varint length prefix serves two purposes:

1. Buffer allocation: the receiver knows the uncompressed payload size upfront.
2. Validation: after decompression, the recovered size must match the prefix.

A size mismatch terminates the stream.

### Protocol ID format

Protocol IDs follow the format:

```
/{prefix}/req/{method}/{version}/{encoding}
```

`prefix` is `leanconsensus`; `encoding` is always `ssz_snappy`.
Concrete method names and versions are fork-specific and live in `specs/<fork>/p2p-interface.md`.

### Response code

A single byte at the start of every response stream:

| Code | Meaning |
| --- | --- |
| `0x00` | Success |
| `0x01` | Invalid request |
| `0x02` | Server error |
| `0x03` | Resource unavailable |

Non-zero codes are followed by an optional human-readable error message in the body.
Clients log the message but do not surface it to the application layer.

## Snappy framing

Snappy framing wraps raw Snappy in checksummed chunks so streams can be processed incrementally with error detection.

### Stream identifier

Every framed stream begins with a fixed 10-byte stream identifier:

```
0xff 0x06 0x00 0x00 's' 'N' 'a' 'P' 'p' 'Y'
```

Breakdown:

| Bytes | Meaning |
| --- | --- |
| `0xff` | Chunk type (stream identifier) |
| `0x06 0x00 0x00` | Chunk length = 6, little-endian |
| `sNaPpY` | Magic bytes |

The identifier may appear multiple times in a stream (e.g. when concatenating compressed streams).

### Chunk format

After the stream identifier, the stream is a sequence of chunks back-to-back:

```
[type: 1 byte] [length: 3 bytes LE] [data: length bytes]
```

The length field does **not** include the 4-byte header.

Chunk types in this codebase:

| Type | Name | Body |
| --- | --- | --- |
| `0x00` | Compressed data | CRC32C + raw Snappy |
| `0x01` | Uncompressed data | CRC32C + literal bytes |
| `0xfe` | Padding | ignored on read |
| `0xff` | Stream identifier | magic bytes (see above) |

A reader rejects unknown chunk types it has not been configured to skip.

### Why framed and not raw

Raw Snappy compresses a single block.
A network protocol needs:

1. Streaming — process data in chunks without buffering everything.
2. Error detection — detect corruption mid-stream.
3. Concatenation — combine multiple compressed substreams.

Framing solves all three.
A receiver can read one chunk at a time, validate its CRC, and dispatch immediately.

## Varint encoding

Length prefixes use unsigned LEB128 varints (the encoding Protocol Buffers uses).

| Value range | Bytes |
| --- | --- |
| 0 – 127 | 1 |
| 128 – 16,383 | 2 |
| 16,384+ | 3+ |

Each byte encodes 7 bits of value.
Bit 7 is the continuation flag: 0 = last byte, 1 = more bytes follow.
The reader assembles 7-bit groups little-endian until it sees a zero continuation bit.

Example, value 300 (`0b100101100`):

- Split into 7-bit groups: `0b10` (high), `0b0101100` (low).
- Encode low group with continuation flag: `0b10101100` = `0xAC`.
- Encode high group (final, no flag): `0b00000010` = `0x02`.
- Wire bytes: `[0xAC, 0x02]`.

## ENR records

ENR (EIP-778) is the open record format used for peer connectivity information.

### Record structure

An ENR is an RLP-encoded list:

```
record = [signature, seq, k1, v1, k2, v2, ...]
```

| Field | Description |
| --- | --- |
| `signature` | 64-byte secp256k1 signature (r ∥ s, no recovery id) |
| `seq` | 64-bit sequence number; increases on each update |
| `k, v` | Sorted key/value pairs; keys are lexicographically ordered |

The signature covers the content `[seq, k1, v1, k2, v2, ...]` (the entire list excluding the signature itself).

Maximum encoded size: 300 bytes.
This fits in a single UDP packet and works inside size-constrained transports such as DNS.

### Identity scheme

The default scheme is `"v4"` (secp256k1):

| Operation | Procedure |
| --- | --- |
| Sign | `secp256k1_sign(keccak256(content))` |
| Verify | Check signature against the secp256k1 public key in the record |
| Node ID | `keccak256(uncompressed_public_key)` |

Cryptographic agility is part of the format: alternative identity schemes may be added later by recording a different `id` key value.

### Text encoding

ENRs are exchanged as URL-safe base64 with the `enr:` prefix.

```
enr:-IS4QHCYrYZbAKW...
```

This encoding is used for bootnode strings, beacon-API responses, and discovery records.

## Transport

The transport layer is QUIC over UDP, implemented via `aioquic`.

The QUIC layer carries libp2p streams; libp2p frames carry gossipsub and reqresp protocols.
TCP fallback is not implemented.

Peer identity uses libp2p PeerId derived from the node's secp256k1 public key.
The peer-id derivation and protocol negotiation logic lives under `node/networking/transport/`.
