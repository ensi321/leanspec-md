---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/store.py
  - src/lean_spec/spec/forks/lstar/aggregation_select.py
  - src/lean_spec/spec/forks/lstar/containers.py
related_prs: [449, 717]
---

# Aggregation — lstar (fork-specific)

<!-- TOC -->

- [Introduction](#introduction)
- [Grouping discipline](#grouping-discipline)
- [Lifecycle](#lifecycle)
- [Store pools](#store-pools)
- [Aggregator step](#aggregator-step)
- [Block proposal step](#block-proposal-step)
- [On-block deconstruction](#on-block-deconstruction)
- [Devnet shape history](#devnet-shape-history)

<!-- /TOC -->

## Introduction

This file describes how lstar **uses** the leanSig aggregation primitives documented in `specs/leansig-aggregation.md`.

The substrate provides Type-1 (single message) and Type-2 (multi-message merge) primitives plus a recursive aggregation API.
lstar specifies:

- How attestations are grouped before aggregation (one Type-1 per distinct `AttestationData`).
- When aggregation runs (interval 2 of each slot).
- How block production folds the per-data Type-1 proofs plus the proposer signature into a single block-level Type-2.

## Grouping discipline

**Key invariant**: in any aggregation step, all contributing validators sign the **same** `AttestationData`.

The grouping key is the `AttestationData` itself (the full container value, not just its hash tree root).
Two attestations differing in `slot`, `head`, `target`, or `source` belong to **different** Type-1 proofs even if many of the same validators signed both.

In the store this shows up as the dict key type for all three attestation pools:

```python
attestation_signatures: dict[AttestationData, set[AttestationSignatureEntry]]
latest_new_aggregated_payloads: dict[AttestationData, set[TypeOneMultiSignature]]
latest_known_aggregated_payloads: dict[AttestationData, set[TypeOneMultiSignature]]
```

A block may carry **up to `MAX_ATTESTATIONS_DATA` (8) distinct `AttestationData` entries**.
The block-import path rejects:

- Duplicate `AttestationData` entries in the body.
- More than 8 distinct entries.

## Lifecycle

```
                            ┌──────────────────────────────────┐
                            │ raw validator XMSS signatures    │
                            │ collected from gossip            │
                            │ (aggregator only)                │
                            └──────────────┬───────────────────┘
                                           │ interval 2
                                           │ aggregate()
                                           ▼
                            ┌──────────────────────────────────┐
                            │ latest_new_aggregated_payloads   │
                            │ Type-1 per AttestationData       │
                            │ not yet counted in head weight   │
                            └──────────────┬───────────────────┘
                                           │ interval 4 (or interval 0 with proposal)
                                           │ accept_new_attestations()
                                           ▼
                            ┌──────────────────────────────────┐
                            │ latest_known_aggregated_payloads │
                            │ Type-1 per AttestationData       │
                            │ counted in LMD-GHOST weight      │
                            └──────────────┬───────────────────┘
                                           │ block proposal
                                           │ produce_block_with_signatures()
                                           ▼
                            ┌──────────────────────────────────┐
                            │ block body carries AttestationData│
                            │ entries; block.proof is the      │
                            │ Type-2 over all of them + proposer│
                            └──────────────────────────────────┘
```

## Store pools

| Pool | Lifecycle stage | Affects head? | Affects safe target? |
| --- | --- | --- | --- |
| `attestation_signatures` | raw, pre-aggregation | no | no |
| `latest_new_aggregated_payloads` | Type-1 aggregated, not yet committed | no | yes |
| `latest_known_aggregated_payloads` | Type-1 in the committed window | yes | no |

The "new" / "known" split is deliberate:

- **Head weight** uses **known** only. Known carries everything that has either been included in a block or migrated through the interval-4 tick.
- **Safe target** uses **new** only. Safe target is a *liveness* signal — it must measure currently-online validators, not historical evidence.

If safe target read from known, a participation collapse would still advance safe target on stale votes accumulated before the drop.

## Aggregator step

Runs at interval 2 for nodes with `is_aggregator=True`.

`aggregate(store)` (see `specs/lstar/fork-choice.md`) processes every `AttestationData` that has either:

- An existing entry in `latest_new_aggregated_payloads[data]` (child proofs), or
- An existing entry in `store.attestation_signatures[data]` (raw signatures).

For each such data entry:

1. **`select_greedily(new.get(data), known.get(data))`** picks a subset of existing Type-1 proofs that maximizes covered validators, preferring new over known.
2. **Fill** with raw gossip signatures for validators not yet covered (sorted by validator index for determinism).
3. **`TypeOneMultiSignature.aggregate(children, raw_xmss, message=hash_tree_root(data), slot=data.slot)`** produces one Type-1 covering the union of all contributing validators.
4. Wrap as `SignedAggregatedAttestation(data, proof)` and add to the broadcast list.

After the loop, `latest_new_aggregated_payloads` is reset and reseeded with the freshly produced proofs.

The broadcast publishes each `SignedAggregatedAttestation` on the aggregation topic (see `specs/lstar/p2p-interface.md`).

## Block proposal step

When the proposer's `produce_block_with_signatures` builds a block:

1. `build_block` returns `(block, post_state, aggregated_attestations, per_attestation_signatures)`.
   The `per_attestation_signatures` list is one Type-1 per `AttestationData` entry in the block body.
2. The validator service signs `hash_tree_root(block)` with the proposal key, producing an XMSS signature.
3. That signature is wrapped as a singleton Type-1 (proposer is the sole signer; participants bitfield names only the proposer's index):

   ```
   proposer_type1 = TypeOneMultiSignature.aggregate(
       children=[],
       raw_xmss=[(proposer_index, proposer_proposal_pubkey, proposer_signature)],
       message=hash_tree_root(block),
       slot=slot,
   )
   ```
4. All Type-1 proofs (per-attestation + proposer singleton) are merged via Type-2 aggregation:

   ```
   type2 = TypeTwoMultiSignature.aggregate(
       parts=[proposer_type1, *per_attestation_signatures],
       public_keys_per_part=[
           [proposer_proposal_pubkey],
           *[attestation_pubkey_list for each per-attestation Type-1],
       ],
   )
   ```
5. The serialized Type-2 proof becomes `SignedBlock.proof`:

   ```
   signed = SignedBlock(block=block, proof=ByteList512KiB(data=type2.proof.data))
   ```

The block carries **one** Type-2 proof covering every attestation in the body plus the proposer's own block-root signature.

## On-block deconstruction

When a peer imports the block via `on_block`:

1. The Type-2 proof is verified **as a whole** against the per-attestation pubkey layouts + the proposer pubkey layout and the corresponding messages (per-`AttestationData` and the block root).
   See `specs/leansig-aggregation.md` for Type-2 verification.
2. The block's own attestations are registered in `latest_known_aggregated_payloads[data]` with an **empty** proof set.
   The Type-2 is not decomposed into per-`AttestationData` Type-1s at import time.

Consequence: a block's attestations contribute **zero weight** to the head computation triggered by that block's import.
The recovered Type-1 proofs reach the pools later through the gossip path (when aggregators in the next round see the same attestations, re-aggregate, and broadcast).
Head weight from block-imported votes is deferred by up to one slot.

The Type-2 → Type-1 split-by-message operation exists on the substrate (`TypeTwoMultiSignature.split_by_msg`) and could in principle be used by validators to deconstruct an imported Type-2 into per-data Type-1s; that path is currently only invoked when a validator wants to re-emit the per-data proofs back into its own pool for future aggregation reuse.

## Devnet shape history

See `specs/lstar/_features/devnet-4/README.md` and `_features/devnet-5/README.md` for the PRs that shaped the current behavior.

Summary:

- **devnet-3 → devnet-4** (PR #449, #496, #510): grouping switched from per-committee to per-`AttestationData`; recursive aggregation primitive landed; block-body Type-1 list per `AttestationData`.
- **devnet-4 → devnet-5** (PR #717): the per-`AttestationData` Type-1 list collapsed into one block-level Type-2; `SignedBlock.signature: BlockSignatures` (structured) replaced by `SignedBlock.proof: ByteList512KiB` (opaque).

The grouping discipline (one proof per distinct `AttestationData`) survives both transitions; what changes is whether the proofs are carried as a list or merged.
