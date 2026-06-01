---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/__init__.py
related_prs: [638]
---

# Fork: lstar

## Identity

| Field | Value |
| --- | --- |
| `NAME` | `"lstar"` |
| `VERSION` | `4` |
| `GOSSIP_DIGEST` | `"12345678"` |
| `previous` | `None` |

lstar is the **root fork** of the Lean consensus chain.
There is no predecessor; `previous = None`.

The `VERSION` value of `4` reflects ordering in the registry; it does **not** correspond to a specific devnet number (devnet-4 vs devnet-5 vs ...).
Future forks register with strictly larger `VERSION` values.

The `GOSSIP_DIGEST` value `"12345678"` is the 4-byte fork digest embedded in every gossipsub topic name (see `specs/lstar/p2p-interface.md`).
Two clients on the same Lean network agree on this digest; a mismatch means the clients are speaking different forks.

## Upgrade

```
def upgrade_state(self, state: State) -> State:
    return state
```

lstar is the root fork, so `upgrade_state` returns the input unchanged.
The method exists to satisfy the `ForkProtocol` abstract surface (see `specs/fork-protocol.md`).

When a successor fork (e.g. `lstar2`) lands, it implements `upgrade_state(state: LstarState) -> Lstar2State` that migrates lstar's container shape into lstar2's container shape.

## Registry placement

```
FORK_SEQUENCE: list[ForkProtocol] = [LstarSpec()]
DEFAULT_REGISTRY: ForkRegistry = ForkRegistry(FORK_SEQUENCE)
```

The package-level `DEFAULT_REGISTRY.current` returns the lstar instance.
The `Store` public alias re-exported from `lean_spec.spec.forks` resolves to `LstarStore`.

When other forks land, callers should switch from the `Store` alias to `DEFAULT_REGISTRY.current.store_class` for fork-aware Store construction.
