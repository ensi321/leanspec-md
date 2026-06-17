---
last_synced_commit: 8cf92a47
source_files:
  - src/lean_spec/spec/forks/lstar/containers/aggregation.py
  - src/lean_spec/spec/crypto/xmss/containers.py
related_prs: [282, 318, 322, 449, 496, 717, 796, 799, 822, 824, 845, 961, 1126, 1131, 1138]
---

# leanSig Aggregation

<!-- TOC -->

- [Introduction](#introduction)
- [Two proof shapes](#two-proof-shapes)
- [The Rust binding boundary](#the-rust-binding-boundary)
  - [Inverse-rate exponent](#inverse-rate-exponent)
  - [Why proofs carry no public keys](#why-proofs-carry-no-public-keys)
- [Containers](#containers)
  - [`SingleMessageAggregate`](#singlemessageaggregate)
  - [`MultiMessageAggregate`](#multimessageaggregate)
- [Operations](#operations)
  - [Single-message aggregation](#single-message-aggregation)
  - [Single-message verification](#single-message-verification)
  - [Multi-message aggregation](#multi-message-aggregation)
  - [Multi-message verification](#multi-message-verification)
  - [Multi-message split by message](#multi-message-split-by-message)
- [Recursive aggregation](#recursive-aggregation)
- [Errors](#errors)
- [Devnet evolution](#devnet-evolution)

<!-- /TOC -->

## Introduction

leanSig provides hash-based multi-signatures over Generalized XMSS, replacing the BLS-style aggregation that beacon-chain consensus uses today.
Proof construction and cryptographic checks live in a Rust binding (`lean-multisig-py`).
This file documents the Python-side container shapes and the operations the spec calls.

The substrate is fork-agnostic.
A fork chooses **how** to group and route proofs through blocks and gossip; the primitives below define **what** a proof is and **how** it composes.

## Two proof shapes

Two proof shapes, distinguished by how many messages a single proof binds.

| Shape | Messages | Slots | Carries | Wire shape |
| --- | --- | --- | --- | --- |
| Type-1 | one | one | participant bitfield + proof bytes | `Container` with two fields |
| Type-2 | many | many | proof bytes only | `Container` with one bytes field |

A Type-1 proof attests that a set of validators signed the **same** message at the **same** slot.
A Type-2 proof binds several Type-1 components into a single object covering distinct messages and slots.

## The Rust binding boundary

Five operations are forwarded to `lean-multisig-py`:

```
aggregate_single_message                  build a Type-1 from raw signatures and/or child Type-1s
verify_single_message_proof               check a Type-1 against a pubkey set, message, and slot
merge_many_single_message_proof           merge many Type-1s into one Type-2
verify_multi_message_proof_with_messages  check a Type-2 against per-component pubkey layouts and bindings
split_multi_message_proof_by_message      extract one Type-1 component from a Type-2 by message
```

The binding names spell out the proof shape: `single_message` is the Type-1 family, `multi_message` the Type-2 family.

A one-shot `setup_prover(mode=LEAN_ENV)` runs at module load.
The mode value is fixed for the process lifetime and selects the Rust backend bytecode (test or production).

### Inverse-rate exponent

The `LOG_INVERSE_RATE` constant (renamed from `LOG_INV_RATE` in PR #1138 under the no-abbreviations rule; pure Python identifier change, no wire impact) forwards an inverse-rate parameter to the SNARK backend.

| Mode | Value |
| --- | --- |
| Test | 1 |
| Production | 2 |

A smaller rate trades verifier cost for prover speed.
Test mode favors prover speed so fixture generation is faster.

### Why proofs carry no public keys

Both Type-1 and Type-2 proofs store proof bytes only; no public keys are embedded.
The verifier supplies pubkeys externally based on the block context it already trusts:

- For Type-1, the verifier looks up pubkeys for the validators named by `participants`.
- For Type-2, the verifier supplies a list of per-component pubkey lists.

This keeps proofs compact at the cost of forcing the caller to reconstruct the original key layout at verification time.
A miscount of supplied keys against participant bits causes `AggregationError` to surface immediately rather than failing opaquely inside the Rust verifier.

## Containers

The Python containers were renamed in PR #799 (`TypeOneMultiSignature` → `SingleMessageAggregate`, `TypeTwoMultiSignature` → `MultiMessageAggregate`) and relocated out of the crypto layer into `forks/lstar/containers/aggregation.py` (PR #796).
The underlying Rust binding still uses the `Type-1` / `Type-2` shape vocabulary, so this chapter continues to refer to proof shapes by that name.

### `SingleMessageAggregate`

```python
class SingleMessageAggregate(Container):
    participants: AggregationBits
    proof: ByteList512KiB
```

Frozen Pydantic container (every spec type is frozen by default since #845).
The Type-1 proof shape; single-message proof aggregating signatures from many validators.
Every validator named by `participants` signed the same message for the same slot.

The message and slot are **not** stored in the container.
The verifier rederives them from the block body it already trusts.

`__hash__` is content-deterministic via SSZ encoding so instances can be inserted into sets and dicts keyed by content identity.

### `MultiMessageAggregate`

```python
class MultiMessageAggregate(Container):
    proof: ByteList512KiB
```

Frozen Pydantic container (every spec type is frozen by default since #845).
The Type-2 proof shape; merged proof covering many distinct messages.
Each component is a single-message proof over its own message.
Merging binds the components into one proof a block can carry whole.

Notably, a multi-message aggregate does **not** store any participant bitfields.
Each component's participants are recovered from the consumer of the proof (the block body, where each aggregated attestation carries its own bitfield).
A signed block stores this proof as a single serialized blob.

`__hash__` is content-deterministic via SSZ encoding.

## Operations

### Single-message aggregation

```
SingleMessageAggregate.aggregate(
    children: list[tuple[SingleMessageAggregate, list[PublicKey]]],
    raw_xmss: list[tuple[ValidatorIndex, PublicKey, Signature]],
    message: Bytes32,
    slot: Slot,
) -> SingleMessageAggregate
```

Fold fresh signatures and child single-message proofs into one single-message proof.

Two kinds of contribution merge:

1. **Fresh signers** contribute a single raw XMSS signature each.
   Each fresh entry carries its validator index, public key, and signature.
2. **Child proofs** contribute an already-aggregated bundle of signers.
   Each child is paired with the public keys it names.

The output names the union of every contributing validator (set union over raw indices and the participant bitfields of all child proofs).

**Why each fresh signer carries its index**: a public key has no inherent validator index.
Pairing the index with each raw entry lets the output bitfield be derived rather than passed in.
An empty list of fresh signers contributes no indices.

The prover compresses all contributions into one proof over the shared message.

### Single-message verification

```
SingleMessageAggregate.verify(
    public_keys: list[PublicKey],
    message: Bytes32,
    slot: Slot,
) -> None
```

Verify against a pubkey set.

The caller must supply exactly one public key per set bit in `participants`, in the same order as the validator indices the bitfield resolves to.
A miscount raises `AggregationError` immediately; the Rust verifier is only invoked when the count is correct.

Failure (cryptographic rejection by the Rust verifier) also surfaces as `AggregationError`.

### Multi-message aggregation

```
MultiMessageAggregate.aggregate(
    parts: list[SingleMessageAggregate],
    public_keys_per_part: list[list[PublicKey]],
) -> MultiMessageAggregate
```

Merge several single-message proofs over **distinct** messages into one multi-message proof.

Each component is checked against the supplied pubkey list before being forwarded to the prover:

1. Each component's `participants` bit count must equal `len(pubkeys)` for that component.
2. Pubkey lists must be in the same order as the participant indices for each component.

Empty input raises `AggregationError`.
The result is a single multi-message proof binding every component to its own message.

The merged multi-message proof stores no public keys; the caller is responsible for tracking the key layout used at construction so the same layout can be supplied at verification or split time.

### Multi-message verification

```
MultiMessageAggregate.verify(
    public_keys_per_message: list[list[PublicKey]],
    messages: list[tuple[Bytes32, Slot]],
) -> None
```

Verify the multi-message proof against its per-component bindings.

Each component is bound to one message-slot pair supplied by the caller.
Without that binding the proof would accept attacker-chosen data resolving to the same keys.
The parallel lists pin every component to the message it actually signed.

Both lists must have equal length; mismatch raises `AggregationError` immediately.

### Multi-message split by message

```
MultiMessageAggregate.split_by_message(
    message: Bytes32,
    public_keys_per_message: list[list[PublicKey]],
    participants: AggregationBits,
) -> SingleMessageAggregate
```

Recover the single-message component bound to one message from a multi-message merge.

The merged proof stores neither the public keys nor the participant bitfields.
The prover needs the original key layout to isolate one component; the caller supplies both, drawn from the block attestation this component binds.

The resulting single-message aggregate carries the supplied `participants` bitfield and the recovered proof bytes.

## Recursive aggregation

The `SingleMessageAggregate.aggregate` operation accepts child proofs alongside fresh signers.
This is the recursive primitive: a previously-aggregated single-message proof can serve as input to a new one covering an expanded validator set without re-collecting raw signatures.

Why this matters: BLS aggregation is essentially free because pairings are linear.
XMSS aggregation produces a fresh proof at every step.
Recursive aggregation lets the proposer fold cross-aggregator Type-1 proofs covering the same `AttestationData` into one proof without re-collecting raw signatures from every validator.
The cost is a Rust-side proving step, which dominates devnet block-time budgets (see PQ devnet evolution below).

The data flow looks like:

```
                                                          slot N
                                                            |
   aggregator A   :  raw_sigs(validators 0..7)              |     produces  Type-1_A  (covers 0..7)
                                                            |
   aggregator B   :  raw_sigs(validators 8..15)             |     produces  Type-1_B  (covers 8..15)
                                                            |
   proposer       :  Type-1.aggregate(                      |
                       children = [Type-1_A, Type-1_B],     |
                       raw_xmss = [],                       |     produces  Type-1   (covers 0..15)
                       message  = attestation_data_root,    |     via recursive aggregation
                       slot     = N,                        |
                     )                                      |
```

Type-2 is then constructed from one Type-1 per distinct `AttestationData` plus the proposer's own message proof.

## Errors

`AggregationError` is the single exception class surfaced by this module.
It wraps:

- Length mismatches between supplied pubkey lists and participant bitfields.
- Empty input where the operation requires at least one component.
- Cryptographic rejections raised by the Rust binding (re-wrapped with context).

Callers should expect any `AggregationError` to be terminal for the current proof construction or verification flow.
Retry without a corrected input is meaningless.

## Devnet evolution

Aggregation shape has changed substantially across PQ devnets.

| Devnet | Shape on the wire | Key PRs |
| --- | --- | --- |
| 0 | no PQ aggregation | — |
| 1 | naive concatenation of raw XMSS signatures | — |
| 2 | first leanMultisig aggregation, per-committee | #282, #318, #322 |
| 3 | per-subnet aggregation; aggregator role and aggregation gossip topic | — |
| 4 | per-`AttestationData` Type-1 list in block; recursive aggregation primitive lands | #449, #496 |
| 5 | single Type-2 per block covering all attestation data plus proposer signature | #717 |

The primitives documented here belong to the devnet-5 surface.
The recursive aggregation API surface landed in #449; the Rust binding caught up in #496.
The single-Type-2-per-block consumer landed in #717.

See `specs/lstar/aggregation.md` for the fork-specific consumer (how lstar groups proofs and constructs Type-2 at the block level).
