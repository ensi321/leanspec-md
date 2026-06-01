# leanspec-md

Personal study companion to `leanEthereum/leanSpec`, written in the `ethereum/consensus-specs` markdown style.

**Not** an alternate source of truth. **Not** executable. Pure reading aid. Pinned to a leanSpec commit; sync workflow surfaces upstream changes.

## Layout (fork-scoped, consensus-specs convention)

Two top-level documentation roots, mirroring mainnet Ethereum's split (`ethereum/consensus-specs` vs `ethereum/beacon-APIs`):

- **`specs/`** — protocol-logic spec (state transition, fork choice, validator duties, p2p, crypto). Mirrors `consensus-specs`.
- **`beacon-api/`** — HTTP API spec (route definitions, schemas, OpenAPI). Mirrors `beacon-APIs`. See `beacon-api/README.md`.

Under `specs/`, two kinds of files:

1. **Substrate** (top-level files) — cross-fork mechanisms and stable primitives that all forks depend on but no single fork owns. Examples: the fork-protocol ABC + registry, SSZ (if it gets documented), merkleization, base XMSS, Poseidon2 / KoalaBear.
2. **Per-fork directories** — each named after a fork (`lstar/`, future `lstar2/`, ...), containing only what that fork **changes or introduces** relative to the previous fork. Stable primitives are not redocumented per fork.

```
specs/
  fork-protocol.md             substrate: ForkProtocol ABC + Spec*Type protocols + registry
  leansig-aggregation.md       substrate: Type-1 / Type-2 multisig primitives + recursive aggregation API
  p2p-substrate.md             substrate: gossipsub mesh, reqresp framing, ENR schema, snappy framing
  lstar/                       current Lean fork
    beacon-chain.md            state transition + containers
    fork-choice.md             store + 3SF-mini fork choice
    validator.md               duty production (attestation + block)
    p2p-interface.md           fork-specific: topic IDs, GOSSIP_DIGEST, message types
    aggregation.md             fork-specific: per-AttestationData grouping + Type-2 block proof
    fork.md                    upgrade rules (lstar is root, previous = None)
    configs/
      lstar.yaml               runtime constants tuned for lstar devnets
    _features/                 incoming devnet work staged here
      devnet-4/README.md       PR #449 — recursive aggregation, dual keys
      devnet-5/README.md       PR #717 — single Type-2 block proof

beacon-api/                    HTTP API spec, mirrors ethereum/beacon-APIs
  apis/
    beacon/                    states, blocks, checkpoints
    debug/                     fork_choice
    node/                      health
    admin/                     aggregator toggle (lean-specific)
  types/
    primitive.yaml, misc.yaml, fork_choice.yaml, api.yaml
    lstar/                     per-fork concrete shapes
  lean-node-oapi.yaml          consolidated OpenAPI
  README.md                    scope, source mapping, sync workflow

engine-api/                    (FUTURE — devnet-6+) CL ↔ EL JSON-RPC interface
                               No leanSpec endpoints today; planned slot for EL integration.
                               Mirror of ethereum/execution-apis when it lands.

scripts/
  pin_upstream.sh              bump pinned SHA after review
  diff_upstream.py             list upstream commits since pinned SHA
  sync_report.py               map changed source files to chapters needing review
.upstream-sha                  pinned leanSpec commit
```

When a new fork lands (e.g. `lstar2`), add `specs/lstar2/` and write only the files that document what lstar2 changes vs lstar. If lstar2 modifies a stable substrate (e.g. adds new SSZ types), promote the relevant substrate file to top-level or extend it.

### Substrate files to write as motivation appears

| Substrate file | Source | Why it's substrate |
| --- | --- | --- |
| `fork-protocol.md` | `src/lean_spec/spec/forks/protocol.py` + `registry.py` | Defines the fork system itself; no fork owns it |
| `ssz.md` (only if needed) | `src/lean_spec/spec/ssz/**` | Subset of `simple-serialize.md`; unchanged across forks |
| `merkleization.md` (only if needed) | `src/lean_spec/spec/crypto/merkleization.py` | hash_tree_root dispatch; unchanged across forks |
| `crypto-xmss.md` (only if needed) | `src/lean_spec/spec/crypto/xmss/**` | XMSS primitive; lstar uses but doesn't define |
| `crypto-poseidon2.md` (only if needed) | `src/lean_spec/spec/crypto/poseidon.py` + `koalabear.py` | Hash primitive; lstar uses but doesn't define |

Skip until a fork actually changes them. Most can stay external (the upstream paper / SSZ spec / etc.) without a local copy.

## Pinned upstream

`.upstream-sha` holds the leanSpec commit this markdown is synced against. Bump via `scripts/pin_upstream.sh` after reviewing diffs.

Default upstream location: `~/Documents/leanSpec`. Override with `LEANSPEC_REPO=/path/to/leanSpec`.

## Workflow

```bash
# 1. Pull upstream
git -C ~/Documents/leanSpec pull

# 2. See what changed since last sync (per-chapter report)
./scripts/sync_report.py

# 3. Read the upstream diffs for affected source files
./scripts/diff_upstream.py --stat

# 4. Update affected chapters under specs/lstar/
#    Update each chapter's `last_synced_commit` frontmatter
#    Update related_prs if a PR landed

# 5. Bump the global pin
./scripts/pin_upstream.sh

# 6. Commit
git add . && git commit -m "sync against leanSpec @ <new sha>"
```

## Per-chapter frontmatter

Every chapter starts with YAML frontmatter:

```markdown
---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/containers.py
related_prs: [449, 717]
---

# Title
```

`sync_report.py` parses `source_files`, runs `git log <last_synced_commit>..HEAD -- <source_files>` against upstream, and lists chapters with non-empty diffs.

## What this is NOT

- An alternate spec — leanSpec Python remains canonical.
- Executable — no pyspec, no fixture extraction, no tests.
- Comprehensive — only what each fork changes. Baseline SSZ / merkleization / XMSS primitives are external standards, not redocumented here.
- A community resource — personal study. Drift is acceptable because the sync workflow surfaces it.

## What this IS

- A personal reading layout that mirrors consensus-specs ergonomics (`specs/<fork>/`).
- A scaffold that survives upstream churn via pinned-SHA + diff reports.
- A learning loop: read upstream PRs, summarize them in narrative markdown, bump the pin.
