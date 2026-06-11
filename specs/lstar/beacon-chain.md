---
last_synced_commit: e7519866
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/_base.py
  - src/lean_spec/spec/forks/lstar/state_transition.py
  - src/lean_spec/spec/forks/lstar/signatures.py
  - src/lean_spec/spec/forks/lstar/slot.py
  - src/lean_spec/spec/forks/lstar/errors.py
  - src/lean_spec/spec/forks/lstar/containers/__init__.py
  - src/lean_spec/spec/forks/lstar/containers/attestation.py
  - src/lean_spec/spec/forks/lstar/containers/block.py
  - src/lean_spec/spec/forks/lstar/containers/checkpoint.py
  - src/lean_spec/spec/forks/lstar/containers/identifiers.py
  - src/lean_spec/spec/forks/lstar/containers/interval.py
  - src/lean_spec/spec/forks/lstar/containers/participation.py
  - src/lean_spec/spec/forks/lstar/containers/state.py
  - src/lean_spec/spec/forks/lstar/containers/validator.py
  - src/lean_spec/spec/forks/lstar/config.py
  - src/lean_spec/spec/forks/lstar/__init__.py
related_prs: [449, 717, 796, 799, 800, 801, 806, 817, 819, 828, 832, 842, 843, 845, 871, 877, 879, 881]
---

# Lean Consensus — Beacon Chain (lstar)

<!-- TOC -->

- [Introduction](#introduction)
- [Custom types](#custom-types)
- [Configuration](#configuration)
- [Containers](#containers)
  - [`GenesisConfig`](#genesisconfig)
  - [`Validator`](#validator)
  - [`Checkpoint`](#checkpoint)
  - [`AttestationData`](#attestationdata)
  - [`Attestation`](#attestation)
  - [`SignedAttestation`](#signedattestation)
  - [`AggregatedAttestation`](#aggregatedattestation)
  - [`SignedAggregatedAttestation`](#signedaggregatedattestation)
  - [`BlockBody`](#blockbody)
  - [`BlockHeader`](#blockheader)
  - [`Block`](#block)
  - [`SignedBlock`](#signedblock)
  - [`State`](#state)
- [Helpers](#helpers)
  - [Slot justifiability](#slot-justifiability)
  - [Proposer selection](#proposer-selection)
  - [Subnet selection](#subnet-selection)
  - [Attestation chain check](#attestation-chain-check)
  - [Aggregation bits ↔ validator indices](#aggregation-bits--validator-indices)
- [Genesis](#genesis)
- [State transition](#state-transition)
  - [`state_transition`](#state_transition)
  - [`process_slots`](#process_slots)
  - [`process_block_header`](#process_block_header)
  - [`process_block`](#process_block)
  - [`process_attestations`](#process_attestations)
- [Justification and finalization (3SF-mini)](#justification-and-finalization-3sf-mini)

<!-- /TOC -->

## Introduction

The lstar fork is the root of Lean consensus.
It bundles **state transition** (block processing) and **fork choice** (`fork-choice.md`) in one fork module; this file documents the state-transition half plus the containers and helpers that both halves share.

Key design choices that distinguish lstar from beacon-chain phase0:

1. **One proposer per slot**, selected round-robin (no RANDAO, no proposer selection from a shuffled set).
2. **No committees**: an `AggregatedAttestation` is a single bitfield over the entire validator registry.
3. **Two-key validators**: each validator carries a separate `attestation_pubkey` and `proposal_pubkey` so XMSS one-time-key indices for the two duties don't collide.
4. **3SF-mini justification**: a slot is eligible for justification only at structured distances from the latest finalized slot (≤5, perfect-square, or pronic).
5. **Sparse historical record**: empty slots are recorded as `ZERO_HASH` in `historical_block_hashes`; `justified_slots` is a sliding bitfield relative to the finalized boundary.

State is intentionally minimal.
There is no participation flag accumulation across epochs, no rewards/penalties machinery, no slashing record.
These are concerns either deferred to later forks or covered by the aggregation proof system rather than state.

## Custom types

| Type | Definition | Description |
| --- | --- | --- |
| `Slot` | `Uint64` | Sequential slot number; carries 3SF-mini justifiability methods (see helpers) |
| `ValidatorIndex` | `Uint64` | Position in the validator registry; carries round-robin proposer helpers |
| `SubnetId` | `Uint64` | Attestation subnet partition (0..committee_count-1) |
| `AggregationBits` | `BaseBitlist` with `LIMIT = VALIDATOR_REGISTRY_LIMIT` | Per-validator participation bitfield for an aggregated attestation |
| `ValidatorIndices` | `SSZList[ValidatorIndex]` with `LIMIT = VALIDATOR_REGISTRY_LIMIT` | Ordered validator index list; converts to and from `AggregationBits` |
| `HistoricalBlockHashes` | `SSZList[Bytes32]` with `LIMIT = HISTORICAL_ROOTS_LIMIT` | Block roots indexed by slot; empty slots carry `ZERO_HASH` |
| `JustifiedSlots` | `BaseBitlist` with `LIMIT = HISTORICAL_ROOTS_LIMIT` | Sliding window of justified-or-not bits starting from `latest_finalized.slot + 1` |
| `JustificationRoots` | `SSZList[Bytes32]` with `LIMIT = HISTORICAL_ROOTS_LIMIT` | Block roots currently accumulating votes |
| `JustificationValidators` | `BaseBitlist` with `LIMIT = HISTORICAL_ROOTS_LIMIT * VALIDATOR_REGISTRY_LIMIT` | Flat concatenation of per-root validator-vote bitfields |

## Configuration

Constants live in `forks/lstar/config.py` (see `specs/lstar/configs/lstar.yaml`).

| Constant | Value | Use |
| --- | --- | --- |
| `SECONDS_PER_SLOT` | 4 | Slot wall-clock duration |
| `INTERVALS_PER_SLOT` | 5 | Sub-slot ticks |
| `MILLISECONDS_PER_INTERVAL` | 800 | Sub-slot interval duration |
| `GOSSIP_DISPARITY_INTERVALS` | 1 | Future-slot tolerance for gossip attestations |
| `JUSTIFICATION_LOOKBACK_SLOTS` | 3 | Justification eligibility window |
| `HISTORICAL_ROOTS_LIMIT` | `2**18` | Maximum historical block roots stored (~12.1 days) |
| `VALIDATOR_REGISTRY_LIMIT` | `2**12` | Maximum validators in registry |
| `IMMEDIATE_JUSTIFICATION_WINDOW` | 5 | First N slots after finalization are always justifiable |
| `ATTESTATION_COMMITTEE_COUNT` | 1 | Number of attestation committees per slot |
| `MAX_ATTESTATIONS_DATA` | 8 | Maximum distinct `AttestationData` entries per block |

## Containers

### `GenesisConfig`

```python
class GenesisConfig(Container):
    genesis_time: Uint64
```

Genesis-time configuration carried by `State` (the slot named `config` on `State` holds a `GenesisConfig`).
Currently holds only `genesis_time`; future forks may extend.

### `Validator`

```python
class Validator(Container):
    attestation_pubkey: Bytes52        # XMSS public key for attestations
    proposal_pubkey: Bytes52           # XMSS public key for block proposals
    index: ValidatorIndex              # position in registry
```

Two distinct XMSS public keys per validator.
The reason is structural: XMSS is **stateful** — each one-time signing index is consumed once.
Sharing a key between high-frequency attestations and rare proposals would coordinate the index space across two duties and create reuse risk under any failover or crash scenario.
Two keys give each duty its own Winternitz chain pool.

Methods:

- `get_attestation_pubkey() -> PublicKey` — decode the XMSS attestation pubkey.
- `get_proposal_pubkey() -> PublicKey` — decode the XMSS proposal pubkey.

### `Checkpoint`

```python
class Checkpoint(Container):
    root: Bytes32                      # block root
    slot: Slot                         # slot of that block
```

A snapshot of a specific block as a (root, slot) pair.
Used for justification and finalization records, attestation source/target/head fields, and store state.

Frozen (`frozen=True`); checkpoints are immutable value objects.

Method:

- `advance_to(candidate: Checkpoint) -> Checkpoint` — return the later of `self` and `candidate`, keeping `self` on a slot tie.
  Forward-only progression used everywhere justified or finalized advances.

### `AttestationData`

```python
class AttestationData(Container):
    slot: Slot                         # voting slot
    head: Checkpoint                   # head observed by validator
    target: Checkpoint                 # target checkpoint
    source: Checkpoint                 # source checkpoint
```

A validator's view of the chain at signing time.
This is the **payload that gets signed**; signature verification binds this exact content.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`).

### `Attestation`

```python
class Attestation(Container):
    validator_id: ValidatorIndex
    data: AttestationData
```

A specific validator's unsigned attestation.
Used as an internal Pydantic model; on the wire validators always send `SignedAttestation` or `AggregatedAttestation`.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`).

### `SignedAttestation`

```python
class SignedAttestation(Attestation):
    signature: Signature
```

A single-validator attestation with its XMSS signature attached.
Used in gossip (single-validator topic) and as the input to aggregation.

The `SignedAttestation` extends `Attestation` by inheritance — adding one field while preserving the parent's structure.
This is the **only** container in lstar that uses signed-via-inheritance; all others compose.
Justified by `Attestation` being lightweight, frozen, and having no need to expose its merkle root separately.

### `AggregatedAttestation`

```python
class AggregatedAttestation(Container):
    aggregation_bits: AggregationBits
    data: AttestationData
```

An attestation aggregated over many validators voting for the same `AttestationData`.
The bitfield names the contributing validators; the data is the shared voted content.

Lstar does **not** use committees; the `aggregation_bits` cover the full validator registry.

### `SignedAggregatedAttestation`

```python
class SignedAggregatedAttestation(Container):
    data: AttestationData
    proof: SingleMessageAggregate
```

The aggregator's broadcast envelope.
Carries the unsigned attestation data and a single-message aggregate proof covering every contributing validator's signature over that data.

Note: composition (not inheritance) of `AggregatedAttestation`, because the participant bitfield lives **inside** the `SingleMessageAggregate.participants` field rather than alongside it.

### `BlockBody`

```python
class BlockBody(Container):
    attestations: AggregatedAttestations
```

A block's payload.
A bounded list of aggregated attestations.
Signatures are not stored here; they fold into the block-level multi-message aggregate proof in `SignedBlock`.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`).

### `BlockHeader`

```python
class BlockHeader(Container):
    slot: Slot
    proposer_index: ValidatorIndex
    parent_root: Bytes32
    state_root: Bytes32
    body_root: Bytes32
```

The fixed-shape summary of a block.
Used in state-transition tracking and state-root caching; never carried separately on the wire.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`).

### `Block`

```python
class Block(Container):
    slot: Slot
    proposer_index: ValidatorIndex
    parent_root: Bytes32
    state_root: Bytes32
    body: BlockBody
```

A complete block.
Note that `Block` and `BlockHeader` share four fields and differ only in their final field (`body` vs `body_root`).
The two are parallel containers, not in an inheritance relationship; this preserves SSZ field ordering as a consensus-critical invariant.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`). PR #843 froze the whole Block family (`BlockBody`, `BlockHeader`, `Block`, `SignedBlock`).

### `SignedBlock`

```python
class SignedBlock(Container):
    block: Block
    proof: MultiMessageAggregate
```

A signed-block envelope.
The `proof` is a `MultiMessageAggregate` (see `specs/leansig-aggregation.md`) binding:

- Every aggregated attestation in `block.body.attestations` (one single-message component per distinct `AttestationData`).
- The proposer's signature over `hash_tree_root(block)` (using the proposer's `proposal_pubkey`).

`SignedBlock` is composed (`block` as a nested field) rather than inheriting `Block` so the block's hash tree root remains a single leaf in the envelope's tree.

Frozen (every spec type is frozen by default since #845, which enforces it once in `StrictBaseModel`).

### `State`

```python
class State(Container):
    # Configuration
    config: GenesisConfig

    # Slot tracking
    slot: Slot
    latest_block_header: BlockHeader

    # Checkpoints
    latest_justified: Checkpoint
    latest_finalized: Checkpoint

    # Historical data
    historical_block_hashes: HistoricalBlockHashes
    justified_slots: JustifiedSlots
    validators: Validators

    # Justification tracking (flattened for SSZ)
    justifications_roots: JustificationRoots
    justifications_validators: JustificationValidators
```

#### Field roles

| Field | Role |
| --- | --- |
| `config` | Genesis configuration carried alongside state |
| `slot` | Current state slot |
| `latest_block_header` | Header of the most recently processed block (carries cached state_root after the next slot tick) |
| `latest_justified` | Most recent justified checkpoint |
| `latest_finalized` | Most recent finalized checkpoint |
| `historical_block_hashes` | Block root per slot; `ZERO_HASH` marks empty slots |
| `justified_slots` | Sliding bitfield: bit at index `i` is the justification status of slot `latest_finalized.slot + 1 + i` |
| `validators` | Registry of validators |
| `justifications_roots` | Sorted block roots currently accumulating votes (unfinalized) |
| `justifications_validators` | Per-root flat vote bitfield; segment `i` covers `len(validators)` bits for root `justifications_roots[i]` |

#### Flattened justification tracking

The `justifications_roots` + `justifications_validators` pair encodes the natural mapping `{root: [vote_per_validator]}` in a flat SSZ-friendly form:

- `justifications_roots[i]` names one tracked root.
- `justifications_validators[i * N : (i + 1) * N]` is that root's per-validator vote bitfield, where `N = len(validators)`.
- Roots are stored **sorted** to guarantee deterministic SSZ encoding across nodes.

`process_attestations` (see below) reconstructs the natural mapping at the start, mutates it, then re-flattens at the end.

## Helpers

### Slot justifiability

```python
def is_justifiable_after(self: Slot, finalized_slot: Slot) -> bool
```

The 3SF-mini rule.
A slot at distance `delta = self - finalized_slot` is justifiable when **any** of:

1. `delta <= IMMEDIATE_JUSTIFICATION_WINDOW` (5).
2. `delta` is a perfect square (`isqrt(delta)**2 == delta`).
3. `delta` is a pronic number (`n(n+1)` for some integer `n`); equivalently, `4*delta+1` is an odd perfect square.

Examples: `delta ∈ {0,1,2,3,4,5, 6,9,12,16,20,25,30,36,...}` are justifiable.
`delta ∈ {7,8,10,11,13,14,15,17,18,19,21,...}` are not.

Why these distances: 3SF-mini constrains justification to a sparse, predictable set of slot positions so finalization can advance through structured gaps; see ethresear.ch on 3SF.

```python
def justified_index_after(self: Slot, finalized_slot: Slot) -> int | None
```

Returns the bitfield index of `self` in `justified_slots` relative to `finalized_slot`:

- `None` when `self <= finalized_slot` (slot is implicitly finalized, no bit tracked).
- Otherwise `int(self - finalized_slot) - 1` (slot `finalized + 1` maps to index 0).

### Proposer selection

```python
@classmethod
def proposer_for_slot(cls, slot: Slot, num_validators: Uint64) -> ValidatorIndex:
    return cls(int(slot) % int(num_validators))
```

Round-robin: validator index `slot % len(validators)` is the proposer for `slot`.
No RANDAO, no shuffled assignment.
Trivial to predict; sufficient for devnet experimentation.

```python
def is_proposer_for(self, slot: Slot, num_validators: Uint64) -> bool
```

Convenience predicate; equivalent to `self == proposer_for_slot(slot, num_validators)`.

### Subnet selection

```python
def compute_subnet_id(self, num_committees: Uint64) -> SubnetId
```

`validator_index % num_committees`.
With `ATTESTATION_COMMITTEE_COUNT = 1`, every validator falls in subnet 0; the helper is in place for future expansion.

### Attestation chain check

```python
def attestation_data_matches_chain(
    attestation_data: AttestationData,
    historical_block_hashes: Sequence[Bytes32],
) -> bool
```

Module-level helper in `state_transition.py`.

Returns True when both the source and target checkpoint roots equal the recorded block roots at their slots.
Returns False when:

- Either checkpoint root is `ZERO_HASH` (empty slot).
- Either checkpoint slot is past the end of the historical view.
- Either recorded chain root differs from the attestation's claim.

Prevents votes about unknown or conflicting forks from contributing to justification.

### Aggregation bits ↔ validator indices

```python
class AggregationBits(BaseBitlist):
    def to_validator_indices(self) -> ValidatorIndices: ...

class ValidatorIndices(SSZList[ValidatorIndex]):
    def to_aggregation_bits(self) -> AggregationBits: ...
```

Round-trip between the two equivalent representations.
Both reject empty input (an aggregated attestation must name at least one validator).
The bit-to-index direction sorts indices ascending; the index-to-bit direction validates that no index exceeds the registry limit.

## Genesis

```python
def generate_genesis(self, genesis_time: Uint64, validators: SSZList[Any]) -> State
```

Builds the genesis state:

1. `Config(genesis_time=genesis_time)`.
2. Genesis block header at slot 0, proposer index 0, zero parent/state roots, empty body root.
3. Empty history (`historical_block_hashes`, `justified_slots`).
4. Empty justification tracking.
5. `latest_justified` and `latest_finalized` both at slot 0 with `ZERO_HASH` root.

The genesis block itself is implicit: there is no `SignedBlock` for slot 0; clients reconstruct the state from the genesis time and validator registry alone.

The first real block processed (slot ≥ 1) sees `parent_header.slot == 0` and triggers the genesis-promotion path in `process_block_header`, which sets `latest_justified.root` and `latest_finalized.root` to the genesis block's actual computed root.

## State transition

The full state-transition function is a four-step pipeline:

### `state_transition`

```python
def state_transition(self, state: State, block: Block) -> State:
    with observe_state_transition():
        advanced = self.process_slots(state, block.slot)
        new_state = self.process_block(advanced, block)
        computed_state_root = hash_tree_root(new_state)
        if block.state_root != computed_state_root:
            raise SpecRejectionError(reason=RejectionReason.STATE_ROOT_MISMATCH, ...)

    return new_state
```

1. Advance the state through empty slots up to `block.slot - 1` (see `process_slots`).
2. Apply the block header and body (`process_block`).
3. Verify that the computed post-state root equals `block.state_root`.

Signature verification happens outside this function (in the `SignatureMixin` methods on `LstarSpec`) before the caller invokes `state_transition`.
A failed root match raises `SpecRejectionError(RejectionReason.STATE_ROOT_MISMATCH)`; the caller treats the block as invalid.

### `process_slots`

```python
def process_slots(self, state: State, target_slot: Slot) -> State
```

Advance through empty slots up to but not including `target_slot`.

For each empty slot:

1. **State root caching**: if `latest_block_header.state_root == ZERO_HASH` (true only for the first empty slot after a block), compute `hash_tree_root(state)` and cache it into the header.
   This is the canonical post-block state root.
2. Increment `state.slot` by 1.

A series of consecutive empty slots performs the caching once (on the first empty slot following the block); subsequent slots simply tick `state.slot`.

`process_slots` is the **only** place `latest_block_header.state_root` is materialized.

### `process_block_header`

```python
def process_block_header(self, state: State, block: Block) -> State
```

Validate the new block header and update header-linked state.

#### Validation

| Check | Failure reason enum |
| --- | --- |
| `block.slot == state.slot` | `BLOCK_SLOT_MISMATCH` |
| `block.slot > parent_header.slot` | `BLOCK_OLDER_THAN_LATEST_HEADER` |
| `block.proposer_index < len(validators)` | `PROPOSER_INDEX_OUT_OF_RANGE` |
| `block.proposer_index.is_proposer_for(state.slot, len(validators))` | `WRONG_PROPOSER` |
| `block.parent_root == hash_tree_root(parent_header)` | `PARENT_ROOT_MISMATCH` |

Any failure raises `SpecRejectionError` (subclass of `AssertionError`) with a `RejectionReason` enum value identifying the failure (see `specs/lstar/fork-choice.md#rejection-errors`).

#### Updates

1. **Genesis promotion**: if `parent_header.slot == 0`, set `latest_justified.root = latest_finalized.root = hash_tree_root(parent_header)`.
   This anchors trust in the genesis block as the chain's first justified-and-finalized checkpoint.
2. **Historical record**: append the parent root to `historical_block_hashes`, then append `ZERO_HASH` for each skipped slot (`num_empty_slots = block.slot - parent_header.slot - 1`).
3. **Justified-slot tracking**: extend `justified_slots` capacity up to `block.slot - 1` (the last materialized slot).
4. **Cache new header**: set `latest_block_header` to a new `BlockHeader` for this block, with `state_root = ZERO_HASH` (filled on the next `process_slots` tick).

### `process_block`

```python
def process_block(self, state: State, block: Block) -> State:
    state = self.process_block_header(state, block)
    return self.process_attestations(state, block.body.attestations)
```

Header validation, then attestation application.
A failure in either stage propagates as `SpecRejectionError`.

### `process_attestations`

```python
def process_attestations(
    self,
    state: State,
    attestations: Iterable[AggregatedAttestation],
) -> State
```

Apply each block-included aggregated attestation, updating justification and finalization per 3SF-mini.

#### Setup

1. Reconstruct the natural justification mapping from the flattened SSZ encoding:
   ```
   justifications[root] = state.justifications_validators[i*N : (i+1)*N]
   ```
   where `N = len(state.validators)` and `i` is the position of `root` in `state.justifications_roots`.
2. Build a `root_to_slot` map for pruning: for each historical root after `latest_finalized.slot`, record its slot.

#### Per-attestation loop

For each `AggregatedAttestation`:

1. **Source must be justified**: skip if `justified_slots.is_slot_justified(finalized_slot, source.slot)` is False.
2. **Target must not already be justified**: skip if the target slot already has its justified bit set.
3. **Chain consistency**: skip if `attestation_data_matches_chain(data, historical_block_hashes)` is False (zero-hash roots, slot out of range, or chain-root mismatch).
4. **Time monotonicity**: skip if `target.slot <= source.slot`.
5. **Target justifiability**: skip if `target.slot.is_justifiable_after(finalized_slot)` is False (3SF-mini rule).
6. **Bits validity** (hard reject, not skip): resolve the voting indices from `aggregation_bits`. An attestation that survives the filters above with no set bits raises `SpecRejectionError(EMPTY_AGGREGATION_BITS)`; a set bit pointing past the registry raises `SpecRejectionError(VALIDATOR_INDEX_OUT_OF_RANGE)`. Trailing unset bits beyond the registry are harmless padding. Signature verification normally rejects an out-of-range bit first, so this guards the unsigned path (added in PR #899).
7. **Record votes**: initialize `justifications[target.root]` to `[False] * len(validators)` if absent; flip each contributing validator's bit to True.
8. **Supermajority check**: if 3 × votes_for_target ≥ 2 × len(validators):
   - Advance `latest_justified` to `target` (only forward; respects `advance_to` semantics).
   - Set `justified_slots[target.slot]` to True.
   - Discard `justifications[target.root]` (tally no longer needed).
   - **Finalization check** (see below).

#### Re-flatten

After the loop, sort the remaining `justifications` roots, then flatten back to SSZ form:

```python
state.justifications_roots = sorted(justifications.keys())
state.justifications_validators = [bit for root in sorted_roots for bit in justifications[root]]
state.justified_slots = justified_slots
state.latest_justified = latest_justified
state.latest_finalized = latest_finalized
```

Roots are sorted to keep SSZ encoding deterministic across clients.

## Justification and finalization (3SF-mini)

### Justification

A block is justified when at least two-thirds of the validator registry has voted for its target checkpoint and the vote satisfies the chain checks above.

The threshold check uses integer arithmetic to avoid floating-point divergence across clients:

```
3 * count >= 2 * len(validators)
```

`latest_justified` advances **forward only**; an out-of-order attestation cannot drag it backwards (the `target.slot > latest_justified.slot` guard).

### Finalization

When a new target becomes justified, check whether finalization can advance.

The rule: `target` finalizes `source` when every slot strictly between `source.slot + 1` and `target.slot - 1` is **not justifiable** after the current finalized slot.

```python
if not any(
    Slot(slot).is_justifiable_after(finalized_slot)
    for slot in range(source.slot + 1, target.slot)
):
    latest_finalized = source
```

In words: there is an unbroken sequence of justifiable slots from the old finalized boundary up to the new justified target, with no justifiable slot in between waiting to receive its own votes.
The earlier source becomes finalized.

### Re-anchoring after finalization

When finalization advances by `delta` slots:

1. Shift `justified_slots` left by `delta` (drop the bits that are now behind the new finalized boundary).
2. Prune any pending `justifications` entries whose tracked root is now at or before the new finalized slot.

The asserted invariant during pruning: every pending justification root must appear in the `root_to_slot` map (a missing root would indicate a logic error in earlier tracking).

After this step, the justification tracking is rebased on the new finalized boundary and the loop continues with subsequent attestations.
