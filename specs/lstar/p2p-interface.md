---
last_synced_commit: 8e28a19
source_files:
  - src/lean_spec/node/networking/gossipsub/topic.py
  - src/lean_spec/node/networking/reqresp/message.py
  - src/lean_spec/spec/forks/lstar/spec.py
related_prs: []
---

# p2p Interface — lstar (fork-specific)

<!-- TOC -->

- [Introduction](#introduction)
- [Fork digest](#fork-digest)
- [Gossip topics](#gossip-topics)
  - [Block topic](#block-topic)
  - [Attestation subnet topics](#attestation-subnet-topics)
  - [Aggregation topic](#aggregation-topic)
- [Request / Response](#request--response)
  - [Status](#status)
  - [BlocksByRoot](#blocksbyroot)
  - [BlocksByRange](#blocksbyrange)
- [Subnet computation](#subnet-computation)

<!-- /TOC -->

## Introduction

This file covers what lstar **adds** to the p2p substrate:

- The 4-byte fork digest used in topic strings.
- Concrete gossip topic names and message types.
- Concrete reqresp protocol IDs and message containers.

The general mesh, codec, snappy, ENR, and transport machinery lives in `specs/p2p-substrate.md` and is shared with future forks.

## Fork digest

```
GOSSIP_DIGEST = "12345678"
```

The 4-byte fork digest embedded in every gossipsub topic name on the lstar network.
Two clients with the same digest are on the same fork; mismatch causes topics to fail parsing with `ForkMismatchError`.

Successor forks ship with different digest values so traffic does not cross fork boundaries.

## Gossip topics

Topic format (from substrate):

```
/leanconsensus/12345678/{topic_name}/ssz_snappy
```

Three topic names are defined for lstar.

### Block topic

```
/leanconsensus/12345678/block/ssz_snappy
```

| Property | Value |
| --- | --- |
| Message type | `SignedBlock` |
| Encoding | SSZ + Snappy frames |
| Subscription | All nodes |

Published by block proposers at interval 0 of the proposal slot.
Receivers run `on_block(store, signed_block)` to import.

### Attestation subnet topics

```
/leanconsensus/12345678/attestation_{subnet_id}/ssz_snappy
```

| Property | Value |
| --- | --- |
| Message type | `SignedAttestation` |
| Encoding | SSZ + Snappy frames |
| Subscription | Per-validator; subnet derived from `validator_index % ATTESTATION_COMMITTEE_COUNT` |

The `{subnet_id}` placeholder is a base-10 integer in `0 .. ATTESTATION_COMMITTEE_COUNT - 1`.

With `ATTESTATION_COMMITTEE_COUNT = 1` in current devnet config, only `attestation_0` is used; the subnet partition exists for future expansion.

Aggregators additionally subscribe to extra subnets configured via `--aggregate-subnet-ids`.

Receivers run `on_gossip_attestation(store, signed_attestation, is_aggregator)`.
Subnet filtering happens at the p2p subscription layer; the handler does no additional subnet check.

### Aggregation topic

```
/leanconsensus/12345678/aggregation/ssz_snappy
```

| Property | Value |
| --- | --- |
| Message type | `SignedAggregatedAttestation` |
| Encoding | SSZ + Snappy frames |
| Subscription | All nodes |

Published by aggregators at interval 2 after running `aggregate(store)`.
Receivers run `on_gossip_aggregated_attestation(store, signed_aggregated_attestation)`.

## Request / Response

ReqResp wire format and response code semantics are in `specs/p2p-substrate.md`.
This section enumerates the lstar-specific protocols and message containers.

### Status

```
/leanconsensus/req/status/1/ssz_snappy
```

The first message exchanged on a new connection.
Allows peers to verify compatibility and determine whether they are on the same chain.

```python
class Status(Container):
    finalized: Checkpoint
    head: Checkpoint
```

SSZ encoding is exactly 80 bytes (two `Checkpoint` containers, each 40 bytes: 32-byte root + 8-byte slot).

Request and response carry the same `Status` shape; the exchange is a symmetric handshake.

### BlocksByRoot

```
/leanconsensus/req/blocks_by_root/1/ssz_snappy
```

Request specific blocks by their root hashes.

**Request:**

```python
class BlocksByRootRequest(Container):
    roots: RequestedBlockRoots   # SSZList[Bytes32, MAX_REQUEST_BLOCKS]
```

**Response:** zero or more `SignedBlock` items, each prefixed by the response code byte and varint length per the substrate codec.

A peer that does not have a requested block omits it from the response (no failure response per missing root).

Primarily used to recover specific recent or missing blocks identified during sync.

### BlocksByRange

```
/leanconsensus/req/blocks_by_range/1/ssz_snappy
```

Request blocks by slot range.

**Request:**

```python
class BlocksByRangeRequest(Container):
    start_slot: Slot
    count: Uint64
```

**Response:** zero or more `SignedBlock` items, one per slot in order, omitting empty slots.

`count` is bounded by `MAX_REQUEST_BLOCKS` (configured in `node/networking/config.py`).

Note: there is no `step` parameter (legacy beacon-chain `BeaconBlocksByRange v1` carried one; Altair deprecated it; lstar treats `step == 1` as implicit).

## Subnet computation

```python
def compute_subnet_id(self: ValidatorIndex, num_committees: Uint64) -> SubnetId:
    return SubnetId(int(self) % int(num_committees))
```

A validator subscribes to the attestation topic for subnet `validator_index % ATTESTATION_COMMITTEE_COUNT`.
With the current value of 1, all validators are in subnet 0.

Aggregators may subscribe to extra subnets beyond their own.
The extra-subnet list is configured via `--aggregate-subnet-ids` and is meaningful only when the node also runs with `--is-aggregator`.
