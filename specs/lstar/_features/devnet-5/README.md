---
last_synced_commit: 8cf92a47
source_files:
  - src/lean_spec/spec/forks/lstar/containers/__init__.py
  - src/lean_spec/spec/forks/lstar/containers/aggregation.py
  - src/lean_spec/spec/forks/lstar/containers/block.py
  - src/lean_spec/spec/forks/lstar/signatures.py
  - src/lean_spec/spec/forks/lstar/spec.py
related_prs: [717, 735, 753, 796, 799, 843]
---

# Devnet-5

## Status

**Active**, as of commit `87943be`.
The current lstar code reflects the devnet-5 shape.

## PRs

| PR | Title | Date | Author |
| --- | --- | --- | --- |
| [#717](https://github.com/leanEthereum/leanSpec/pull/717) | Aggregated block proof - devnet5 | 2026-05-20 | Anshal Shukla (merged by tcoratger) |
| [#735](https://github.com/leanEthereum/leanSpec/pull/735) | metrics: attestation aggregate coverage gauges with subnet labels | 2026-05-20 | Partha |
| [#753](https://github.com/leanEthereum/leanSpec/pull/753) | (additional aggregation hot-path metrics) | 2026-05-21 | Partha |

## Headline change

Block-level Type-2 proof.
One serialized `MultiMessageAggregate` per block covers:

- Every aggregated attestation in `block.body.attestations`.
- The proposer's signature over `hash_tree_root(block)`.

Replaces devnet-4's structured `BlockSignatures` (per-`AttestationData` Type-1 list plus separate proposer signature).

## Container changes (PR #717)

### Removed

- `BlockSignatures` (had `attestation_signatures: AttestationSignatures` + `proposer_signature: Signature`).
- `AttestationSignatures` (was `SSZList[AggregatedSignatureProof]`).

### Changed

- `SignedBlock` envelope reshaped:

  ```python
  # Before (devnet-4)
  class SignedBlock(Container):
      block: Block
      signature: BlockSignatures
  
  # After (devnet-5, initial #717 shape)
  class SignedBlock(Container):
      block: Block
      proof: ByteList512KiB
  ```

  PR #843 later typed the field directly as `proof: MultiMessageAggregate` (see the "Why opaque `ByteList512KiB`" section below for the original rationale and its reversal).

- `BlockBody.attestations` docstring change:
  - Before: "Signatures are in BlockSignatures."
  - After: "Signatures are folded into the block-level proof."

### Unchanged

- `Block` class itself: slot, proposer_index, parent_root, state_root, body fields are identical.
- `BlockBody`: still `attestations: AggregatedAttestations`.
- `AggregatedAttestation`: still `aggregation_bits + data`.

The structural delta is concentrated in the envelope, not the block.

## Type-2 multisig (the underlying primitive)

`MultiMessageAggregate.aggregate(parts, public_keys_per_part)` merges several Type-1 proofs over distinct messages into one proof.

Block-level usage:

1. Compose Type-1 proofs:
   - One per `AttestationData` entry in `block.body.attestations` (signing message = `hash_tree_root(attestation_data)`).
   - One singleton over the proposer's block-root signature (signing message = `hash_tree_root(block)`).
2. Merge via `Type-2.aggregate(parts, public_keys_per_part)`.
3. Embed serialized bytes as `SignedBlock.proof`.

Verification path: `Type-2.verify(public_keys_per_message, messages)` checks the merged proof against the per-component pubkey layouts and the per-component message-slot bindings.

## Why opaque `ByteList512KiB` (historical, reversed in #843)

The original #717 shape used `proof: ByteList512KiB` rather than wrapping the typed `MultiMessageAggregate` container, with three justifications at the time:

- Wrapping adds a 4-byte SSZ offset prefix with no semantic gain (the wrapper container has no consensus-visible fields).
- Hash tree root is identical either way (single-field Container merkleization short-circuits to the inner root).
- The bytes are opaque to Python (only the leanMultisig Rust binding parses them); naming the field as bytes is honest about the opacity.

PR #843 reversed this decision and typed `SignedBlock.proof` as `MultiMessageAggregate` directly, freezing the whole Block family in the process. Current code carries the typed proof.

## Other PR #717 changes worth noting

- `MAX_ATTESTATIONS_DATA` reduced to 8 (commit message: "limit max attestation data to 8").
- Proof size cap reduced to 500 KiB (commit message: "reduce max proof size to 500KiB").
- Block-attestation deconstruction added: aggregators (and proposers) deconstruct a multi-message aggregate back into per-`AttestationData` single-message proofs via `split_by_message` and re-emit them into the new pool for future aggregation reuse.
  Triggered "even in case of proposer, not just for aggregators" per commit history.

## Open questions raised during devnet-5 review

Per KB digest of pq-interop chat (Anshal, Partha, tcoratger, Emile threads in late May 2026):

- **Skip aggregation when only one signature?** (#747, #748) Partha proposed skip-aggregate as an aggregator optimization. Emile's caveat: Type-2 still internally needs a trivial singleton Type-1 even when the upstream aggregator skips raw aggregation.
  Anshal's resolution: skip at the aggregator level but keep `aggregate()` capable of n=1 for the block-packing edge case.
- **Goldfish replaces 3SF-mini heartbeat** in devnet-5 plans (per KB), but the lstar code at `87943be` still ships the 3SF-mini justifiability rules.
  Confirm with upstream before assuming the heartbeat shape has actually flipped.

## Performance posture

Devnet-5 trades some recursive build cost for a smaller wire payload and simpler verification:

- The block carries one proof bytes blob instead of an SSZ list of per-`AttestationData` proofs.
- Verification calls `verify_multi_message_proof_with_messages` once instead of N separate Type-1 verifications.

But the recursive aggregation cost (the binding constraint of devnet-4) does not vanish — it shifts to the Type-2 merge step at block construction time.

Per KB digest of devnet-4 perf chatter, the underlying XMSS recursive proving step is still on the order of 1.5–6 seconds per block.
Devnet-5 makes block size smaller; it does not yet make proving faster.
