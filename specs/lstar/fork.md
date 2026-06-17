---
last_synced_commit: ec63d1cf
source_files:
  - src/lean_spec/spec/forks/lstar/spec.py
  - src/lean_spec/spec/forks/lstar/_base.py
  - src/lean_spec/spec/forks/lstar/__init__.py
related_prs: [638, 808, 817, 1028, 1141]
---

# Fork: lstar

## Identity

| Field | Value |
| --- | --- |
| `NAME` | `"lstar"` |
| `VERSION` | `4` |
| `GOSSIP_DIGEST` | `"12345678"` |

lstar is the **root fork** of the Lean consensus chain.
There is no predecessor.
A `previous` fork-chaining classvar once recorded this as `None`, but #1028 removed it: with lstar standing alone the registry orders forks by `VERSION` and never walks a `previous` link.

The `VERSION` value of `4` reflects ordering in the registry; it does **not** correspond to a specific devnet number (devnet-4 vs devnet-5 vs ...).
Future forks register with strictly larger `VERSION` values.

The `GOSSIP_DIGEST` value `"12345678"` is the 4-byte fork digest embedded in every gossipsub topic name (see `specs/lstar/p2p-interface.md`).
Two clients on the same Lean network agree on this digest; a mismatch means the clients are speaking different forks.

## Upgrade

lstar declares no state-migration hook.
An `upgrade_state` no-op once existed to satisfy the `ForkProtocol` abstract surface, but #1028 dropped both it and the abstract method while lstar is the only fork (see `specs/fork-protocol.md`).

When a successor fork (e.g. `lstar2`) lands, the migration machinery returns: lstar2 implements an `upgrade_state(state: LstarState) -> Lstar2State` that migrates lstar's container shape into lstar2's.

## Registry placement

```
FORK_SEQUENCE: list[ForkProtocol] = [LstarSpec()]
DEFAULT_REGISTRY: ForkRegistry = ForkRegistry(FORK_SEQUENCE)
```

The package-level `DEFAULT_REGISTRY.current` returns the lstar instance.
The `Store` public alias re-exported from `lean_spec.spec.forks` resolves to `LstarStore`.

`LstarSpec` itself is a thin facade composing mixins (PR #817): `StateTransitionMixin`, `SignatureMixin`, `BlockProductionMixin`, `ForkChoiceMixin`, `AggregationMixin`, `TimelineMixin`, `ValidatorDutiesMixin`, and `LstarSpecBase`. Each mixin owns one concern; the chapters under `specs/lstar/` map to them rather than to a monolithic `spec.py`.

`LstarSpecBase` declares the lstar genesis contract directly with the concrete `Validators` and `State` types (PR #1141):

```python
@abstractmethod
def generate_genesis(self, genesis_time: Uint64, validators: Validators) -> State: ...
```

`StateTransitionMixin.generate_genesis` matches that signature exactly — no `SSZList[Any]` parameter, no `isinstance` assert. See `specs/fork-protocol.md` for why the declaration lives here instead of on the cross-fork `ForkProtocol`.

When other forks land, callers should switch from the `Store` alias to `DEFAULT_REGISTRY.current.store_class` for fork-aware Store construction.
