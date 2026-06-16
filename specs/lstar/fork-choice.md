---
last_synced_commit: 49ef89f4
source_files:
  - src/lean_spec/spec/forks/lstar/fork_choice.py
  - src/lean_spec/spec/forks/lstar/aggregation.py
  - src/lean_spec/spec/forks/lstar/timeline.py
  - src/lean_spec/spec/forks/lstar/containers/store.py
  - src/lean_spec/spec/forks/lstar/containers/interval.py
  - src/lean_spec/spec/forks/lstar/errors.py
  - src/lean_spec/spec/forks/lstar/_base.py
related_prs: [449, 717, 796, 799, 800, 802, 805, 806, 818, 819, 820, 827, 833, 845, 871, 879, 888, 892, 972, 1001, 1020]
---

# Fork Choice — lstar

<!-- TOC -->

- [Introduction](#introduction)
- [Interval schedule](#interval-schedule)
- [The `Store`](#the-store)
  - [Pinned anchor fields](#pinned-anchor-fields)
  - [Block and state caches](#block-and-state-caches)
  - [Attestation pools](#attestation-pools)
  - [`AttestationSignatureEntry`](#attestationsignatureentry)
- [Store construction](#store-construction)
- [Handlers](#handlers)
  - [`on_block`](#on_block)
  - [`on_gossip_attestation`](#on_gossip_attestation)
  - [`on_gossip_aggregated_attestation`](#on_gossip_aggregated_attestation)
  - [`on_tick`](#on_tick)
- [Rejection errors](#rejection-errors)
- [Validation](#validation)
  - [`validate_attestation`](#validate_attestation)
  - [Signature verification](#signature-verification)
- [Head computation (LMD-GHOST)](#head-computation-lmd-ghost)
  - [`extract_attestations_from_aggregated_payloads`](#extract_attestations_from_aggregated_payloads)
  - [`compute_block_weights`](#compute_block_weights)
  - [`_compute_lmd_ghost_head`](#_compute_lmd_ghost_head)
  - [`update_head`](#update_head)
- [Safe target](#safe-target)
  - [`update_safe_target`](#update_safe_target)
- [Pool migration and pruning](#pool-migration-and-pruning)
  - [`accept_new_attestations`](#accept_new_attestations)
  - [`prune_stale_attestation_data`](#prune_stale_attestation_data)
- [Aggregation step](#aggregation-step)
  - [`aggregate`](#aggregate)
  - [`select_greedily`](#select_greedily)

<!-- /TOC -->

## Introduction

Fork choice in lstar uses **LMD-GHOST** over the latest known aggregated attestation set, starting from `latest_justified` and descending the heaviest subtree.

State is held in a `Store` object that is mutated in place by handlers (`on_block`, `on_gossip_attestation`, `on_gossip_aggregated_attestation`, `on_tick`).

The slot is subdivided into **five intervals** (800 ms each at 4-second slots).
Each interval triggers specific maintenance actions on the store.
This sub-slot scheduling is the central novelty relative to beacon-chain phase0, where fork-choice ticks are per-slot.

## Interval schedule

| Interval | Duration | Action |
| --- | --- | --- |
| 0 | 0–800 ms | Block proposal; if proposal present, migrate new → known via `accept_new_attestations` |
| 1 | 800–1600 ms | Validators cast attestations (gossip only; no store action) |
| 2 | 1600–2400 ms | Aggregators run `aggregate`, broadcast `SignedAggregatedAttestation` |
| 3 | 2400–3200 ms | `update_safe_target` (deepest 2/3-supermajority block) |
| 4 | 3200–4000 ms | `accept_new_attestations` (migrate new → known) |

`INTERVALS_PER_SLOT = 5`; `MILLISECONDS_PER_INTERVAL = 800`.

The interval index resets to 0 at the start of each slot.

## The `Store`

```python
class Store[StateT: Container, BlockT: Container](StrictBaseModel):
    time: Interval                  # current time in intervals since genesis
    config: GenesisConfig           # chain configuration
    head: Bytes32                   # canonical head root
    safe_target: Bytes32            # current safe target root
    latest_justified: Checkpoint
    latest_finalized: Checkpoint
    blocks: dict[Bytes32, BlockT]
    states: dict[Bytes32, StateT]
    validator_index: ValidatorIndex | None
    attestation_signatures: dict[AttestationData, set[AttestationSignatureEntry]]
    latest_new_aggregated_payloads: dict[AttestationData, set[SingleMessageAggregate]]
    latest_known_aggregated_payloads: dict[AttestationData, set[SingleMessageAggregate]]
```

`LstarStore = Store[State, Block]` is the concrete specialization owned by the lstar fork.

### Pinned anchor fields

| Field | Role |
| --- | --- |
| `time` | Current time as an interval count since genesis |
| `config` | Chain configuration constants (mirrored from state) |
| `head` | Result of running LMD-GHOST on the current pool contents |
| `safe_target` | Deepest descendant of `latest_justified` with at least 2/3 supermajority among **new** payloads |
| `latest_justified` | Most recent justified checkpoint advanced forward only |
| `latest_finalized` | Most recent finalized checkpoint advanced forward only |
| `validator_index` | Local validator's index, or `None` for passive nodes |

### Block and state caches

| Field | Role |
| --- | --- |
| `blocks` | Block root → `Block` (every block that might participate in fork choice) |
| `states` | Block root → post-state of that block |

Both maps are append-only within a finalized window; `prune_stale_attestation_data` does not remove blocks or states (those are pruned by storage, not fork choice).

### Attestation pools

Three pools track attestation evidence in three lifecycle stages.

| Pool | Type | Contains |
| --- | --- | --- |
| `attestation_signatures` | `dict[AttestationData, set[AttestationSignatureEntry]]` | Per-validator raw XMSS signatures collected by aggregators from gossip |
| `latest_new_aggregated_payloads` | `dict[AttestationData, set[SingleMessageAggregate]]` | Single-message aggregates not yet contributing to head weight |
| `latest_known_aggregated_payloads` | `dict[AttestationData, set[SingleMessageAggregate]]` | Single-message aggregates contributing to head weight |

Lifecycle:

```
gossip individual attestation
       ↓ (on_gossip_attestation, aggregator only)
attestation_signatures
       ↓ (aggregate, interval 2)
latest_new_aggregated_payloads
       ↓ (accept_new_attestations, interval 0 or 4)
latest_known_aggregated_payloads
       ↓ (used by update_head)
fork choice weight
```

Block-imported attestations bypass the new pool: `on_block` records the data key in `latest_known_aggregated_payloads` with an empty proof set.
The multi-message aggregate carried in `SignedBlock.proof` is verified as a whole, not decomposed; per-attestation proofs reach the pools later through the gossip path.

Consequence: a block's own attestations contribute **zero weight** to the head computation triggered by that block's import.
Head weight from block-imported votes is deferred by up to one slot.

### `AttestationSignatureEntry`

```python
class AttestationSignatureEntry(NamedTuple):
    validator_index: ValidatorIndex
    signature: Signature
```

Single validator's raw XMSS signature for a specific `AttestationData`.
Used as elements in `attestation_signatures[data]`.

## Store construction

```python
def create_store(
    self,
    state: SpecStateType,
    anchor_block: SpecBlockType,
    validator_index: ValidatorIndex | None,
) -> LstarStore
```

Constructs the store from a `(state, anchor_block, validator_index)` triple.
The anchor is either the genesis pair (when the node starts fresh) or a checkpoint-sync result.

`create_store` lives on `ForkChoiceMixin` and treats the anchor block as the new genesis for fork choice: both `latest_justified` and `latest_finalized` are seeded from the anchor block's root and slot, irrespective of what the anchor state's embedded checkpoints say.
The anchor block's `state_root` must match `hash_tree_root(state)`, else construction asserts.

## Handlers

### `on_block`

```python
def on_block(self, store: LstarStore, signed_block: SignedBlock) -> LstarStore
```

Process a new signed block.

1. **Idempotency**: skip if the block root is already in `store.blocks`.
2. **Parent availability**: assert `parent_state = store.states[block.parent_root]` exists.
3. **Body validity**:
   - Reject blocks with duplicate `AttestationData` entries.
   - Reject blocks with more than `MAX_ATTESTATIONS_DATA` (8) distinct entries.
4. **Verify signatures**: `verify_signatures(signed_block, parent_state.validators)` checks the block's multi-message aggregate proof against the parent state's validator pubkeys.
5. **State transition**: `state_transition(parent_state, block)` produces the post-state. Signature verification has already happened at step 4 — `state_transition` no longer carries a `valid_signatures` parameter (dropped in #806).
6. **Checkpoint propagation**: only `latest_justified` advances via `advance_to` here (forward only; ties keep the existing root). `latest_finalized` is **no longer** advanced in `on_block` — as of #1001 it is recomputed from the canonical head inside `update_head` (step 8), not maintained as an independent monotonic max.
7. **Register block in known pool**: for each `aggregated_attestation` in the body, ensure `latest_known_aggregated_payloads[data]` exists (with empty proof set if new).
8. **Recompute head**: `update_head(store)` — this also recomputes `latest_finalized` (see below).
9. **Prune** if `latest_finalized.slot` advanced: `prune_stale_attestation_data(store)`.

### `on_gossip_attestation`

```python
def on_gossip_attestation(
    self,
    store: LstarStore,
    signed_attestation: SignedAttestation,
    is_aggregator: bool = False,
) -> LstarStore
```

Process a single-validator attestation received via gossip.

1. **`validate_attestation(store, signed_attestation.data)`**: see below.
2. **State lookup**: assert `store.states[data.target.root]` exists; needed for the validator's pubkey.
3. **Validator bounds**: assert `signed_attestation.validator_index.is_valid(len(validators))`.
4. **Signature verify**: `TARGET_SIGNATURE_SCHEME.verify(pubkey, slot, hash_tree_root(data), signature)`.
5. **Aggregator storage**: if `is_aggregator`, record the entry in `attestation_signatures[data]`.
   Non-aggregator nodes validate then drop.

Subnet filtering happens at the p2p subscription layer; only attestations from subscribed subnets reach this handler.

### `on_gossip_aggregated_attestation`

```python
def on_gossip_aggregated_attestation(
    self,
    store: LstarStore,
    signed_attestation: SignedAggregatedAttestation,
) -> LstarStore
```

Process an aggregator's broadcast.

1. **`validate_attestation(store, data)`**.
2. **State lookup**: assert `store.states[data.target.root]` exists.
3. **Validator bounds**: assert every validator named by `proof.participants` is in range.
4. **Single-message verification**: `proof.verify(public_keys=[...attestation_pubkey...], message=hash_tree_root(data), slot=data.slot)`.
5. **Pool insert**: add the proof to `latest_new_aggregated_payloads[data]`.

A failed verification surfaces as `AssertionError`; the aggregator's broadcast is dropped without affecting other handlers.

### `on_tick`

```python
def on_tick(
    self,
    store: LstarStore,
    target_interval: Interval,
    has_proposal: bool,
    is_aggregator: bool = False,
) -> tuple[LstarStore, list[SignedAggregatedAttestation]]
```

Advance store time to `target_interval` by stepping forward one interval at a time, calling `tick_interval` at each step.
Returns the new store plus any aggregates produced during the walk.

```python
def tick_interval(
    self,
    store: LstarStore,
    has_proposal: bool,
    is_aggregator: bool = False,
) -> tuple[LstarStore, list[SignedAggregatedAttestation]]
```

Advance `store.time` by one interval and dispatch interval-specific work.

| Current interval | Condition | Action |
| --- | --- | --- |
| 0 | `has_proposal` | `accept_new_attestations(store)` |
| 2 | `is_aggregator` | `store, new_aggregates = aggregate(store)` |
| 3 | — | `update_safe_target(store)` |
| 4 | — | `accept_new_attestations(store)` |
| 1 | — | (no action) |

The returned `new_aggregates` list is non-empty only at interval 2 for aggregators; otherwise empty.

## Rejection errors

PR #871 replaced bare `AssertionError`s with a typed `SpecRejectionError(AssertionError)` carrying a `RejectionReason` enum (defined in `spec/forks/lstar/errors.py`).
The new error subclasses `AssertionError`, so existing rejection handlers keep working unchanged; the testing framework now matches on the language-neutral reason enum instead of substring-matching English prose.

Every rejection across `state_transition`, `fork_choice`, `signatures`, `validator_duties`, and `participation` raises `SpecRejectionError(reason=RejectionReason.<KIND>, message=...)`.
The block-level proof failure (`INVALID_BLOCK_PROOF`) is split out from the single-signature failure (`INVALID_SIGNATURE`) so the two are distinguishable in test vectors and observability.

## Validation

### `validate_attestation`

```python
def validate_attestation(
    self,
    store: LstarStore,
    attestation_data: AttestationData,
) -> None
```

Pre-flight validation applied before signature verification.

#### Availability check

| Assertion | Reason enum |
| --- | --- |
| `data.source.root in store.blocks` | `UNKNOWN_SOURCE_BLOCK` |
| `data.target.root in store.blocks` | `UNKNOWN_TARGET_BLOCK` |
| `data.head.root in store.blocks` | `UNKNOWN_HEAD_BLOCK` |

#### Topology check

| Assertion | Reason enum |
| --- | --- |
| `data.source.slot <= data.target.slot` | `SOURCE_AFTER_TARGET` |
| `data.head.slot >= data.target.slot` | `HEAD_OLDER_THAN_TARGET` |

#### Consistency check

| Assertion | Reason enum |
| --- | --- |
| `store.blocks[data.source.root].slot == data.source.slot` | `SOURCE_SLOT_MISMATCH` |
| `store.blocks[data.target.root].slot == data.target.slot` | `TARGET_SLOT_MISMATCH` |
| `store.blocks[data.head.root].slot == data.head.slot` | `HEAD_SLOT_MISMATCH` |

#### Ancestry check (PR #833)

| Assertion | Reason enum |
| --- | --- |
| `data.source` is an ancestor of `data.target` in `store.blocks` | `SOURCE_NOT_ANCESTOR_OF_TARGET` |
| `data.target` is an ancestor of `data.head` in `store.blocks` | `TARGET_NOT_ANCESTOR_OF_HEAD` |

The ancestry check walks `store.blocks` from the descendant up through `parent_root` and asserts the ancestor's root and slot match exactly.
Before #833 a vote could name a `(source, target, head)` triple where the three did not actually lie on the same chain, and the vote would still contribute weight; this is now rejected at attestation validation.

#### Head-slot lower bound (PR #1020)

| Assertion | Reason enum |
| --- | --- |
| `data.slot >= data.head.slot` | `ATTESTATION_SLOT_BEFORE_HEAD` |

A vote cannot have observed its head before that head existed, so the vote slot must not precede the slot of the head block it names.
This anchors the wire `slot` from below by a known block and enforces the 3SF semantics where the slot records when the head was seen.
It also keeps the unbounded wire `slot` clear of the `2**64` overflow edge handled by the time check below.

#### Time check

```python
admission_horizon_interval = int(store.time) + int(GOSSIP_DISPARITY_INTERVALS)
max_admissible_slot = admission_horizon_interval // int(INTERVALS_PER_SLOT)
if int(data.slot) > max_admissible_slot:
    raise SpecRejectionError(reason=RejectionReason.ATTESTATION_TOO_FAR_IN_FUTURE, ...)
```

Honest validators emit votes only after their slot has begun.
A small disparity margin (1 interval) absorbs clock skew between peers.

PR #1020 reworked this to compare in **slot units with plain-int arithmetic** rather than first lifting the wire slot into an `Interval`.
The two are equivalent, but multiplying a near-`2**64` wire slot into intervals overflowed the range-checked unsigned constructor and crashed the node — a one-message remote denial of service — so the comparison is now done before any interval is constructed.

### Signature verification

`verify_signatures(signed_block, parent_state.validators)` is the entry point for block-level signature checks.
It walks `signed_block.proof` (the `MultiMessageAggregate` block proof) against the per-aggregated-attestation data plus the block-root binding for the proposer signature.

Cross-reference: `specs/leansig-aggregation.md` for multi-message verification mechanics.

## Head computation (LMD-GHOST)

### `extract_attestations_from_aggregated_payloads`

```python
def extract_attestations_from_aggregated_payloads(
    self,
    aggregated_payloads: dict[AttestationData, set[SingleMessageAggregate]],
    latest_finalized_slot: Slot,
) -> dict[ValidatorIndex, AttestationData]
```

The dead `store` parameter was dropped in PR #888.

Returns a `validator → most_recent_AttestationData` map.

For each `(data, proof)` pair, for each participant validator in `proof.participants`, record `data` if no later record exists for that validator.
"Later" = strictly higher `data.slot`.

This is the **latest-message** part of LMD-GHOST: each validator's most recent vote counts; earlier votes are dropped.

PR #972 folded the `head.slot > latest_finalized.slot` staleness predicate **into** this extractor (the new `latest_finalized_slot` argument): a vote whose head sits at or below the finalized slot is skipped here. Previously all three call sites (`update_head`, `_compute_lmd_ghost_head`, `update_safe_target`) pre-filtered their pool with the same comprehension before calling; they now pass the pool directly plus `store.latest_finalized.slot`. Iteration order and results are unchanged, so the first-seen-wins tie-break is preserved.

### `compute_block_weights`

```python
def compute_block_weights(self, store: LstarStore) -> dict[Bytes32, int]
```

For each validator's latest vote, walk from the voted head up through ancestors while the block exists in the store and its slot is above `latest_finalized.slot`.
Each visited ancestor accumulates one unit of weight.

The walk terminates when:

- The current root is unknown (missing parent during partial sync).
- The block's slot is at or below the finalized slot.

PR #818 clamped the attestation target walk to the finalized boundary explicitly so votes targeting blocks at or below the finalized slot are skipped rather than walked into pre-finalized history.
PR #820 dropped aggregated payloads whose target falls at or below the finalized slot from fork-choice weighting; they remain in storage but no longer move head weight.

### `_compute_lmd_ghost_head`

```python
def _compute_lmd_ghost_head(
    self,
    store: LstarStore,
    start_root: Bytes32,
    attestations: dict[ValidatorIndex, AttestationData],
    min_score: int = 0,
) -> Bytes32
```

The LMD-GHOST greedy walk.

1. Compute weights by walking each validator's vote up through ancestors above `start_slot` (same logic as `compute_block_weights` but with `start_slot = store.blocks[start_root].slot`).
2. Build a `parent → [children]` adjacency map over `store.blocks`, skipping children with `weights[root] < min_score` when a threshold is set.
3. Descend from `start_root`, at each step choosing the child with the highest weight.
   Ties break **lexicographically larger hash wins** (`max(children, key=lambda x: (weights[x], x))`).
4. Stop when no children remain; that leaf is the head.

The `min_score` parameter is zero for normal head computation and `ceil(num_validators * 2 / 3)` for safe-target computation (see below).

### `update_head`

```python
def update_head(self, store: LstarStore) -> LstarStore
```

1. `attestations = extract_attestations_from_aggregated_payloads(latest_known_aggregated_payloads, latest_finalized.slot)`.
2. `store.head = _compute_lmd_ghost_head(store, latest_justified.root, attestations)`.
3. **Recompute `latest_finalized` from the new head (PR #1001).** Read the finalized slot named by the head's post-state (`store.states[head].latest_finalized.slot`) and recover its root by climbing the head's own ancestor chain down to that slot. Guard the walk as the other ancestor walks do — stop if a parent is absent from the store — and keep the existing trusted anchor when no block sits exactly at the finalized slot (covers a checkpoint-sync boot or a skipped finalized slot). The head and finalized checkpoint are then committed in one `model_copy`.

The head is always a descendant of `latest_justified.root` by construction.

Deriving `latest_finalized` here — rather than as an independent monotonic max over every imported block's post-state (the pre-#1001 behavior in `on_block` and block production) — keeps the finalized checkpoint **on the head chain**. Before the fix, a fork that finalized a higher slot but then lost head selection latched its finalized checkpoint in the store; `get_attestation_target` (which reads `store.latest_finalized`) then disagreed with the state transition (which validates targets against the canonical state's `latest_finalized`), `is_justifiable_after` rejected every advancing target, and finalization froze. Fork choice is deliberately **not** clamped to finalized descendants: leanSpec finalization is order-dependent, so a fork that finalizes a higher slot can still lose head selection, and clamping would latch the head onto that losing fork. The attestation source keeps using `store.latest_justified` (the LMD anchor).

## Safe target

The **safe target** is the deepest descendant of `latest_justified` that has at least 2/3 supermajority support among **new** payloads.
Validators use it to decide which block is safe to attest to.

### `update_safe_target`

```python
def update_safe_target(self, store: LstarStore) -> LstarStore
```

Runs at interval 3.

1. `num_validators = len(store.states[store.head].validators)`.
2. `min_target_score = ceil(num_validators * 2 / 3)`.
3. `attestations = extract_attestations_from_aggregated_payloads(latest_new_aggregated_payloads, latest_finalized.slot)`.
4. `safe_target = _compute_lmd_ghost_head(store, latest_justified.root, attestations, min_score=min_target_score)`.

**Why only the new pool**: safe target is an availability signal, not durable knowledge.

- A block is safe when 2/3 of currently-online validators vote for a descendant.
- "Known" carries block-included, previously migrated, and self-attestations.
- Those reflect historical knowledge, not current liveness.
- Counting them would advance safe target on stale evidence after a participation collapse.

## Pool migration and pruning

### `accept_new_attestations`

```python
def accept_new_attestations(self, store: LstarStore) -> LstarStore
```

Migrate `latest_new_aggregated_payloads → latest_known_aggregated_payloads`:

1. Iterate keys in **deterministic order** (known keys first in insertion order, then new keys in insertion order — PR #892 fixed a bug where the merge iterated `known.keys() | new.keys()`, which is hash-ordered and let two clients pick different forks for equivocating validators).
2. For each `(data, proofs)` pair, union into the merged dict via copy-on-write (`{**known, **{data: known.get(data, set()) | proofs}}` style — PR #888 replaced the in-place `.setdefault().update()` pattern after #845 made the Store frozen).
3. Clear new.
4. Recompute head with the expanded known pool.

Runs at interval 0 (if proposal) and interval 4 (unconditional).

### `prune_stale_attestation_data`

```python
def prune_stale_attestation_data(self, store: LstarStore) -> LstarStore
```

Remove attestation data that can no longer influence fork choice — entries whose `target.slot <= latest_finalized.slot`.

Applies to all three pools (`attestation_signatures`, `latest_new_aggregated_payloads`, `latest_known_aggregated_payloads`).

Called from `on_block` whenever finalization advances.

## Aggregation step

### `aggregate`

```python
def aggregate(
    self,
    store: LstarStore,
) -> tuple[LstarStore, list[SignedAggregatedAttestation]]
```

Lives on `AggregationMixin` (`spec/forks/lstar/aggregation.py`); runs at interval 2 for aggregators.
Turns raw validator signatures into compact single-message aggregates.

For each unique `AttestationData` that has either a new payload or a raw gossip signature:

1. **Select** (`select_greedily` — see below):
   - Pick existing proofs that maximize validator coverage.
   - Prefer **new** payloads over **known** (new = uncommitted to chain; known = previously accepted).
   - Output: `(child_proofs, covered_validator_indices)`.
2. **Fill**:
   - For every validator with a raw gossip signature whose index is **not** in `covered`, build a `(validator_index, attestation_pubkey, signature)` entry.
   - Sort by validator index for determinism.
3. **Skip** if `not raw_entries and len(child_proofs) < 2` — a single child proof is already valid, nothing to combine.
4. **Aggregate**:
   - Pair each child proof with its participants' attestation pubkeys.
   - Call `SingleMessageAggregate.aggregate(children, raw_xmss, message=hash_tree_root(data), slot=data.slot)`.
   - Wrap in `SignedAggregatedAttestation(data=data, proof=proof)`.

After all data entries are processed:

- `store.latest_new_aggregated_payloads` is reset and reseeded with the freshly produced proofs.
- `store.attestation_signatures` entries consumed by the new payloads are removed.

Returns the updated store plus the list of produced `SignedAggregatedAttestation`s for broadcast.

### `select_greedily`

Source: `spec/forks/lstar/aggregation.py` (folded back from the deleted `aggregation_select.py` module in #827).

A greedy proof-selection helper used by `aggregate`.
Given the new and known payload sets for one `AttestationData`, pick a subset of proofs that maximizes covered validators while preferring new over known.

Output is a tuple `(selected_proofs, covered_validator_indices)`.
The caller then fills the uncovered validators using raw gossip signatures.

Cross-reference: `specs/lstar/aggregation.md` for the broader picture of how lstar consumes the leanSig aggregation primitives.
