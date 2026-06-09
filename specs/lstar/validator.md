---
last_synced_commit: 8e28a19
source_files:
  - src/lean_spec/spec/forks/lstar/validator_duties.py
  - src/lean_spec/spec/forks/lstar/block_production.py
  - src/lean_spec/spec/forks/lstar/aggregation.py
  - src/lean_spec/spec/forks/lstar/signatures.py
  - src/lean_spec/spec/forks/lstar/timeline.py
  - src/lean_spec/spec/forks/lstar/slot.py
  - src/lean_spec/spec/forks/lstar/errors.py
  - src/lean_spec/node/validator/service.py
  - src/lean_spec/node/validator/registry.py
related_prs: [449, 717, 796, 799, 800, 808, 817, 819, 827, 843, 845, 871, 874, 878]
---

# Validator — lstar

<!-- TOC -->

- [Introduction](#introduction)
- [Validator key pair](#validator-key-pair)
- [Duties by interval](#duties-by-interval)
- [Attestation production](#attestation-production)
  - [`get_attestation_target`](#get_attestation_target)
  - [`produce_attestation_data`](#produce_attestation_data)
- [Block production](#block-production)
  - [`get_proposal_head`](#get_proposal_head)
  - [`produce_block_with_signatures`](#produce_block_with_signatures)
  - [`build_block`](#build_block)
- [Signing](#signing)
  - [Attestation signing](#attestation-signing)
  - [Block-root signing](#block-root-signing)
- [Aggregator role](#aggregator-role)
- [Validator service (runtime)](#validator-service-runtime)

<!-- /TOC -->

## Introduction

A validator in lstar has two duties:

1. **Attestation**: every slot, sign an `AttestationData` reflecting the validator's view of the chain.
2. **Block proposal**: when round-robin proposer selection picks this validator's index, build and sign a block.

A subset of validators additionally serve as **aggregators**, combining gossiped individual attestations into `SingleMessageAggregate` proofs (the Type-1 shape).

Each duty signs with a **different XMSS key**:

- Attestations sign with `Validator.attestation_pubkey` / matching secret.
- Block-root endorsements sign with `Validator.proposal_pubkey` / matching secret.

The two-key split is structural: XMSS is stateful (each one-time-key index is consumed once); sharing a key across high-frequency attestations and rare proposals would create index-reuse risk.

## Validator key pair

```python
class ValidatorKeyPair(StrictBaseModel):
    attestation_keypair: KeyPair    # for attestation signatures
    proposal_keypair: KeyPair       # for proposer block-root signatures
```

Each `KeyPair` carries a `PublicKey` and a `SecretKey`.
The two pairs are independent XMSS trees with their own Winternitz chain pools.

A validator's two `Bytes52` pubkeys (`attestation_pubkey`, `proposal_pubkey`) carried in the `Validator` container correspond to the SSZ-encoded `PublicKey.root` plus `parameter` halves of these XMSS pairs.

## Duties by interval

| Interval | Action |
| --- | --- |
| 0 | Block proposal (if this validator is proposer for current slot) |
| 1 | Attestation production + signing + gossip broadcast |
| 2 | Aggregator: build `SingleMessageAggregate`s from gossip signatures, broadcast |
| 3 | (no validator action; safe-target updates server-side) |
| 4 | (no validator action; pool migration server-side) |

Sub-slot timing is essential: late attestations may miss the supermajority window for their target slot; late proposals get reorged.

## Attestation production

### `get_attestation_target`

```python
def get_attestation_target(self, store: LstarStore) -> Checkpoint
```

Compute the attestation target checkpoint.

1. Start at `store.head`.
2. Walk back up to `JUSTIFICATION_LOOKBACK_SLOTS` (3) steps while the candidate slot is strictly higher than the safe-target's slot.
3. Walk back further as needed until the candidate slot satisfies `is_justifiable_after(latest_finalized.slot)` (the 3SF-mini rule).
4. Return `Checkpoint(root, slot)` for the resulting block.

The two walks balance liveness (advance the target) against safety (don't outpace safe target, don't pick a slot the chain cannot justify).

### `produce_attestation_data`

```python
def produce_attestation_data(self, store: LstarStore, slot: Slot) -> AttestationData
```

1. `head = Checkpoint(root=store.head, slot=store.blocks[store.head].slot)`.
2. `target = get_attestation_target(store)`.
3. `source = store.latest_justified` (the validator's local view of the latest justified checkpoint).
4. Construct `AttestationData(slot, head, target, source)`.

This is the unsigned payload the validator then signs with its attestation key.

## Block production

### `get_proposal_head`

```python
def get_proposal_head(self, store: LstarStore, slot: Slot) -> tuple[LstarStore, Bytes32]
```

Prepare the store for block proposal at `slot`.

1. Advance store time to the first interval of `slot` via `on_tick(target_interval=Interval.from_slot(slot), has_proposal=True)`.
2. Run `accept_new_attestations` to migrate new → known payloads.
3. Return `(store, store.head)`.

The proposal head reflects the latest chain view after processing all pending attestations.
Building on stale state would risk the block being orphaned by other clients with a fresher view.

### `produce_block_with_signatures`

```python
def produce_block_with_signatures(
    self,
    store: LstarStore,
    slot: Slot,
    validator_index: ValidatorIndex,
) -> tuple[LstarStore, Block, list[SingleMessageAggregate]]
```

Top-level block production entry point.

1. **Get proposal head**: `(store, head_root) = get_proposal_head(store, slot)`.
2. **Authorize**: assert `validator_index.is_proposer_for(slot, num_validators)`.
3. **Build**: call `build_block` with the head state, slot, proposer index, parent root, known block roots, and aggregated payloads.
4. **Invariant check**: assert `final_post_state.latest_justified.slot >= store.latest_justified.slot`.
   The fixed-point loop in `build_block` must close any justified divergence between the store and the head chain.
   Failure indicates the loop didn't converge.
5. **Persist**: store the block and post-state under `hash_tree_root(block)`.
6. **Advance checkpoints**: forward-only via `advance_to`.
7. **Prune** if finalization advanced.

Returns `(store, final_block, per_attestation_signatures)`.

The returned `signatures` list contains **per-attestation single-message proofs**, **unmerged**.
The validator service then:

- Signs `hash_tree_root(block)` with the proposal key.
- Wraps that signature into a singleton `SingleMessageAggregate`.
- Merges all single-message proofs (including the proposer's) into the block-level `MultiMessageAggregate` carried by `SignedBlock.proof`.

### `build_block`

```python
def build_block(
    self,
    state: State,
    slot: Slot,
    proposer_index: ValidatorIndex,
    parent_root: Bytes32,
    known_block_roots: AbstractSet[Bytes32],
    aggregated_payloads: dict[AttestationData, set[SingleMessageAggregate]] | None = None,
) -> tuple[Block, State, list[AggregatedAttestation], list[SingleMessageAggregate]]
```

Construct a block from a pre-state by:

1. Finding `AttestationData` entries in `aggregated_payloads` whose `source` matches the current `state.latest_justified`.
2. Greedily selecting proofs that maximize new validator coverage.
3. Applying the state transition function to the candidate body.
4. If justification advances (the new `latest_justified` is higher), repeat with the new checkpoint.
5. Otherwise, return the block with the chosen attestations, the post-state, the chosen `AggregatedAttestation` list, and the corresponding per-attestation single-message proofs.

The fixed-point loop is necessary because adding attestations can advance the justified checkpoint, which unlocks more `AttestationData` entries whose source now matches.

The output block carries the correct `state_root` (computed from the converged post-state) so other nodes' state-transition verification succeeds.

## Signing

### Attestation signing

After `produce_attestation_data`, the validator:

```python
data = produce_attestation_data(store, slot)
message = hash_tree_root(data)
signature = TARGET_SIGNATURE_SCHEME.sign(
    secret_key=validator_keypair.attestation_keypair.secret_key,
    slot=slot,
    message=message,
)
signed = SignedAttestation(validator_index=index, data=data, signature=signature)
gossip_publish(signed, topic=f"/leanconsensus/{GOSSIP_DIGEST}/attestation_{subnet}/ssz_snappy")
```

Signs with the **attestation key**.
`slot` is bound into the signature so the same message at a different slot would not verify.

### Block-root signing

After `produce_block_with_signatures`, the validator:

```python
block_root = hash_tree_root(final_block)
proposer_signature = TARGET_SIGNATURE_SCHEME.sign(
    secret_key=validator_keypair.proposal_keypair.secret_key,
    slot=slot,
    message=block_root,
)
# Wrap as singleton single-message aggregate
proposer_singleton = SingleMessageAggregate.aggregate(
    children=[],
    raw_xmss=[(validator_index, proposal_pubkey, proposer_signature)],
    message=block_root,
    slot=slot,
)
# Merge proposer singleton with per-attestation singles into block-level multi-message proof
block_proof = MultiMessageAggregate.aggregate(
    parts=[proposer_singleton, *per_attestation_signatures],
    public_keys_per_part=[[proposal_pubkey], *attestation_pubkey_lists],
)
signed_block = SignedBlock(block=final_block, proof=block_proof)
gossip_publish(signed_block, topic=f"/leanconsensus/{GOSSIP_DIGEST}/block/ssz_snappy")
```

Signs the **block root** with the **proposal key**.

The multi-message merge folds the proposer signature plus every per-attestation single-message proof into one `MultiMessageAggregate` — the `proof` field of `SignedBlock`. PR #843 typed this field directly (previously `ByteList512KiB`).

## Aggregator role

An aggregator is a validator that additionally:

1. Subscribes to its assigned attestation subnet(s).
2. Stores incoming `SignedAttestation` signatures in `store.attestation_signatures`.
3. At interval 2, runs `aggregate(store)` (see `specs/lstar/fork-choice.md`) to build `SignedAggregatedAttestation` payloads from accumulated signatures.
4. Broadcasts each produced aggregate on the aggregation topic.

Aggregator selection is configured at runtime via the `--is-aggregator` CLI flag plus `--aggregate-subnet-ids`.
There is no on-chain selection; the role is operator-determined for now.

The aggregator's own validator role still applies (it produces and signs attestations like any other validator); aggregation is additive.

## Validator service (runtime)

The validator service in `node/validator/` drives the duty schedule:

1. **`ValidatorRegistry`** (`node/validator/registry.py`) loads validator key pairs from disk via `from_keys_directory(node_id, base_dir)`.
2. **`ValidatorService`** (`node/validator/service.py`) hooks into the node's clock and store.
3. At each interval tick, the service dispatches:
   - Interval 0 (if proposer): `produce_block_with_signatures` + broadcast.
   - Interval 1: `produce_attestation_data` + sign + broadcast per validator.
   - Interval 2 (if aggregator): `aggregate` + broadcast.

The service is the integration point between the spec's pure-function handlers and the node's I/O machinery (gossip, storage, clock).
