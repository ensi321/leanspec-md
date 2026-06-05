# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Personal study companion to `leanEthereum/leanSpec`, written in `ethereum/consensus-specs` markdown style. It is **not** an alternate source of truth, **not** executable, and **not** comprehensive — only what each fork changes vs. the previous fork. `leanSpec` Python remains canonical; this repo is a pinned, narrative reading aid.

Pinned to a single upstream commit (`.upstream-sha`); a sync workflow surfaces upstream changes. Drift is expected and accepted.

## Layout (two parallel doc roots)

Mirrors mainnet Ethereum's `consensus-specs` vs `beacon-APIs` split.

- `specs/` — protocol logic (state transition, fork choice, validator, p2p, crypto).
  - Top-level files = **substrate**: cross-fork primitives no single fork owns (`fork-protocol.md`, `leansig-aggregation.md`, `p2p-substrate.md`).
  - `specs/<fork>/` (currently only `lstar/`) = **per-fork**: only what that fork changes or introduces. Contains `beacon-chain.md`, `fork-choice.md`, `validator.md`, `p2p-interface.md`, `aggregation.md`, `fork.md`, plus `configs/<fork>.yaml` and `_features/devnet-N/` for staged incoming work.
- `beacon-api/` — HTTP API spec (route definitions, schemas, OpenAPI). Mirrors `beacon-APIs`. Endpoints are `/lean/v0/*`, not `/eth/v1/*`. Consolidated OpenAPI lives in `beacon-api/lean-node-oapi.yaml`. Per-fork concrete shapes under `beacon-api/types/<fork>/`. See `beacon-api/README.md` for source-file mapping.
- `engine-api/` — planned (devnet-6+), CL ↔ EL JSON-RPC. Does not exist yet.

When a new fork lands (e.g. `lstar2`), add `specs/lstar2/` with only the files documenting changes vs `lstar`. Promote a substrate file only if a fork modifies it.

## Per-chapter frontmatter (load-bearing)

Every `.md` chapter and every `.yaml` under `beacon-api/` carries frontmatter that `sync_report.py` parses to detect drift. Two forms:

**Markdown (`.md`)** — plain YAML fence:
```markdown
---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
related_prs: [449, 717]
---
```

**YAML (`.yaml`)** — comment-fence form, because the file also has real YAML payload:
```yaml
# Frontmatter
# ---
# last_synced_commit: 87943be
# source_files:
#   - src/lean_spec/node/api/endpoints/states.py
# related_prs: []
# ---
```

`source_files` paths are relative to the upstream `leanSpec` repo, not this one. When you edit a chapter to reflect upstream changes, update its `last_synced_commit` to the SHA you synced against. The global `.upstream-sha` is bumped separately, last.

## Sync workflow

Default upstream location: `~/Documents/leanSpec`. Override with `LEANSPEC_REPO=/path/to/leanSpec`.

```bash
git -C ~/Documents/leanSpec pull       # 1. pull upstream
./scripts/sync_report.py               # 2. per-chapter dirty report
./scripts/diff_upstream.py --stat      # 3. read upstream diffs
# 4. edit affected chapters; bump each chapter's last_synced_commit
./scripts/pin_upstream.sh              # 5. bump global .upstream-sha (interactive)
git add . && git commit -m "sync against leanSpec @ <sha>"
```

`scripts/sync_report.py [-v]` walks `specs/**` and `beacon-api/**` for `*.md`, `*.yaml`, `*.yml`; for each chapter runs `git log <chapter-pin>..HEAD -- <source_files>` against `LEANSPEC_REPO` and prints `DIRTY` chapters with new commits. Chapter pin falls back to global `.upstream-sha` if frontmatter omits `last_synced_commit`.

`scripts/diff_upstream.py [--stat]` is a thin wrapper around `git log --oneline <pin>..HEAD` in the upstream repo.

`scripts/pin_upstream.sh` writes upstream `HEAD` into `.upstream-sha` after confirmation. Run this *after* editing chapters, not before.

## Conventions

- No build, no tests, no linter. The scripts are standalone Python 3 with no deps (the YAML parser in `sync_report.py` is a hand-rolled subset on purpose).
- Cross-link from API docs into spec chapters using the `specs/<fork>/<file>.md#anchor` form (see `beacon-api/apis/beacon/states/finalized.yaml` for the pattern).
- Substrate files only exist when motivated by a real cross-fork concern. Don't preemptively redocument SSZ / merkleization / XMSS / Poseidon2 — they live as external standards until a fork changes them.
- `specs/<fork>/_features/devnet-N/` is where incoming devnet work is staged before it gets folded into the main chapters. Treat these as scratchpads, not canonical.
- Admin endpoints (`beacon-api/apis/admin/`) are leanSpec additions with no `beacon-APIs` precedent — fine to invent shape, but flag the divergence in the file.
