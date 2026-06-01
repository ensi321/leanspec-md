# beacon-api/

Lean HTTP API documentation, mirroring `ethereum/beacon-APIs` repo structure.

**Parallels** `specs/` rather than nesting under it — same split mainnet Ethereum uses (`ethereum/consensus-specs` vs `ethereum/beacon-APIs`).

## Scope

- `apis/` — endpoint route definitions, grouped by namespace.
- `types/` — schema definitions for request/response payloads. Per-fork shapes under `types/<fork>/`.
- `lean-node-oapi.yaml` — consolidated OpenAPI spec (mirror of upstream's `beacon-node-oapi.yaml`).

Out of scope (per upstream beacon-APIs): root-level `dist/`, `deploy/`, `validator-flow.md`, dictionaries, redoc config. This is reading-only documentation, not a redoc-publishable site.

## Layout

```
beacon-api/
  apis/
    beacon/
      states/            GET /lean/v0/states/finalized
      blocks/            GET /lean/v0/blocks/finalized
      checkpoints/       GET /lean/v0/checkpoints/justified
    debug/
      fork_choice.yaml   GET /lean/v0/fork_choice
    node/
      health.yaml        GET /lean/v0/health
    admin/
      aggregator.yaml    GET/POST /lean/v0/admin/aggregator
  types/
    primitive.yaml       Root, Slot, ValidatorIndex, ...
    misc.yaml            shared misc shapes
    fork_choice.yaml     fork-choice graph response
    api.yaml             common API envelope/error types
    lstar/               per-fork concrete shapes (Block, State, Attestation, ...)
  lean-node-oapi.yaml    consolidated OpenAPI
```

## Mapping to leanSpec source

| Spec file | Source |
| --- | --- |
| `apis/node/health.yaml` | `src/lean_spec/node/api/endpoints/health.py` |
| `apis/beacon/states/*` | `src/lean_spec/node/api/endpoints/states.py` |
| `apis/beacon/checkpoints/*` | `src/lean_spec/node/api/endpoints/checkpoints.py` |
| `apis/debug/fork_choice.yaml` | `src/lean_spec/node/api/endpoints/fork_choice.py` |
| `apis/admin/aggregator.yaml` | `src/lean_spec/node/api/endpoints/aggregator.py` + `node/api/aggregator_controller.py` |
| (metrics endpoint) | `src/lean_spec/node/api/endpoints/metrics.py` — Prometheus, not in OpenAPI scope |

## Sync workflow

Same pinned-SHA convention as `specs/`. Per-file frontmatter:

```yaml
---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/node/api/endpoints/states.py
  - src/lean_spec/node/api/routes.py
related_prs: []
---
```

`sync_report.py` walks `**/*.yaml` and `**/*.md` under both `specs/` and `beacon-api/`, so this directory is covered automatically.

## Naming notes

- Filename `lean-node-oapi.yaml` (not `beacon-node-oapi.yaml`) — actual endpoints are `/lean/v0/*`, not `/eth/v1/*`. Honest naming over strict structural mirroring.
- Group dirs (`beacon/`, `debug/`, `node/`) keep the upstream beacon-APIs naming convention for muscle memory.
- `admin/` is a leanSpec addition (aggregator role toggle); no direct beacon-APIs precedent.
