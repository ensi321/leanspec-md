---
last_synced_commit: e7519866
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/containers/__init__.py
  - src/lean_spec/spec/forks/lstar/containers/aggregation.py
  - src/lean_spec/spec/forks/lstar/containers/store.py
  - src/lean_spec/spec/forks/lstar/aggregation.py
  - src/lean_spec/spec/forks/lstar/signatures.py
related_prs: [449, 496, 510]
---

# Devnet-4

## Status

**Historical.** Merged before commit `87943be`. The current lstar shape is the devnet-5 shape (see `../devnet-5/`).
This file documents what devnet-4 introduced and what changed under it.

## PRs

| PR | Title | Date | Author |
| --- | --- | --- | --- |
| [#449](https://github.com/leanEthereum/leanSpec/pull/449) | separate attestation and proposal keys & recursive aggregation API update - Devnet4 | 2026-03-16 | Anshal Shukla |
| [#496](https://github.com/leanEthereum/leanSpec/pull/496) | Update multisig bindings to support recursive aggregation | (post-#449) | Anshal Shukla |
| [#510](https://github.com/leanEthereum/leanSpec/pull/510) | aggregate attestation data while block building | (post-#496) | (various) |

## Container changes (PR #449)

### Removed

- `BlockWithAttestation` — was a wrapper bundling `Block` with a proposer's separate `Attestation`.
- `SignedBlockWithAttestation` — envelope around `BlockWithAttestation` (replaced by `SignedBlock`).

### New / changed

- `SignedBlock(Container)` — simpler envelope: `block: Block` + `signature: BlockSignatures`.
- `Validator` gained a second pubkey field:
  - Before: `pubkey: Bytes52`.
  - After: `attestation_pubkey: Bytes52` + `proposal_pubkey: Bytes52`.
  - Method rename: `get_pubkey()` → `get_attestation_pubkey()` + `get_proposal_pubkey()`.

### Proposer signing model

- Before: proposer included their own `Attestation` payload alongside the block.
- After: proposer signs `hash_tree_root(block)` directly with a **separate proposal key**.

### Aggregation API (PR #449 scaffolded, #496 implemented)

`AggregatedSignatureProof.aggregate()` signature changed:

- Before: `(participants, public_keys, signatures, message, slot, mode)`.
- After: `(xmss_participants, children, raw_xmss, message, slot, mode)`.

The new `children: Sequence[Self]` parameter accepts other aggregated proofs as recursive inputs.
Validation: at least one raw signature or one child proof; if only children, at least two.

The API was added in #449 but the Rust binding scaffold (`lean-multisig-py`) did not yet implement the recursive path.
PR #496 caught the binding up.

### Block-body aggregation per `AttestationData` (PR #510)

`build_block` and `aggregate` now group attestations by `AttestationData` rather than per-committee.
The block body carries one Type-1 proof per distinct `AttestationData` (up to `MAX_ATTESTATIONS_DATA = 8`).

Cross-committee votes for the **same** `AttestationData` collapse into one Type-1 via recursive aggregation, dropping the per-committee redundancy of devnet-3.

## Why the dual key model

XMSS is stateful — each one-time signing index can be used exactly once.
Sharing a key between high-frequency attestations (every slot) and rare proposals (~ 1/N slots) would coordinate the two duties' index spaces and create reuse risk under any failover or crash scenario.

Two independent XMSS key pairs give each duty its own Winternitz chain pool, so:

- An attestation duty consumes only attestation-key indices.
- A proposal duty consumes only proposal-key indices.

## Why recursive aggregation

Per-`AttestationData` grouping means multiple aggregators may each independently produce Type-1 proofs covering disjoint subsets of validators for the **same** `AttestationData`.

Without recursion, the block would carry both Type-1 proofs.
With recursion, the proposer (or a later aggregator) merges them into one Type-1 covering the union — by feeding the existing Type-1 proofs as `children` to a fresh `aggregate` call.

The trade: recursion compresses the block payload at the cost of an additional Rust-side proving step (1.5–6 seconds per block per KB perf data) instead of trivial concatenation.

## Devnet-4 performance signal (per KB digests, May 2026)

- Recursive XMSS aggregation: 1.5–6 s per window depending on payload count.
- Non-recursive aggregation: under 600 ms.
- Per-client measured aggregate-build times under devnet-4:
  - zeam: 2.0–2.9 s
  - ethlambda: 0.4–1.7 s
  - grandine: 1.05–1.18 s
  - gean: 1.8–2.7 s
  - ream: metric not yet exposed
- Verification cost: 35–71 ms across all clients (cheap).

The recursive build cost is the binding constraint that motivated devnet-5's "one Type-2 per block" change.
