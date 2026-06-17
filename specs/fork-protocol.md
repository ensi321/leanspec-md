---
last_synced_commit: f92d8c05
source_files:
  - src/lean_spec/spec/forks/__init__.py
  - src/lean_spec/spec/forks/protocol.py
  - src/lean_spec/spec/forks/registry.py
related_prs: [638, 686, 800, 804, 882, 883, 1028, 1141]
---

# Fork Protocol

<!-- TOC -->

- [Introduction](#introduction)
- [Two layers of typing](#two-layers-of-typing)
- [Structural protocols](#structural-protocols)
  - [`SpecSSZType`](#specssz-type)
  - [`SpecStateType`](#specstatetype)
  - [`SpecBlockType`](#specblocktype)
  - [`SpecStoreType`](#specstoretype)
- [Nominal interface: `ForkProtocol`](#nominal-interface-forkprotocol)
  - [Identity fields](#identity-fields)
  - [Container class slots](#container-class-slots)
  - [Abstract methods](#abstract-methods)
- [Registry](#registry)
  - [`ForkRegistry`](#forkregistry)
  - [`FORK_SEQUENCE` and `DEFAULT_REGISTRY`](#fork_sequence-and-default_registry)
- [Adding a new fork](#adding-a-new-fork)

<!-- /TOC -->

## Introduction

The fork-protocol layer defines how Lean consensus dispatches across forks.
A fork is a versioned implementation that supplies a complete set of consensus container classes plus two lifecycle hooks: genesis construction and store construction.

Two complementary typing mechanisms cooperate here:

- A **nominal** abstract base class (`ForkProtocol`) defines what every fork must extend and implement.
- A stack of **structural** protocols (`Spec*Type`) defines the minimum read surface each concrete container must expose. Structural protocols are duck-typed: a class satisfies a protocol if it has the right methods and properties, regardless of inheritance.

Together they let runtime services (sync, chain, networking, validator, API) operate against the protocol surface without depending on a specific fork's concrete classes.

## Two layers of typing

| Layer | Mechanism | Enforcement | Use |
| --- | --- | --- | --- |
| Nominal | `abc.ABC` subclass | inheritance + abstract method dispatch | every fork must inherit `ForkProtocol` and implement its abstract methods |
| Structural | `typing.Protocol` | shape-matching at type-check time | concrete containers (Block, State, etc.) need only expose the named methods and properties; no inheritance required |

A fork's concrete container classes (`Block`, `State`, `Store`, ...) do **not** inherit from any `Spec*Type` protocol.
They satisfy a protocol by exposing the same field names and methods, and the type checker accepts the substitution wherever the protocol type is expected.

## Structural protocols

Four structural protocols remain after PR #882 collapsed the unused payload-type zoo: `SpecSSZType`, `SpecStateType`, `SpecBlockType`, and `SpecStoreType`.
Payload class slots on `ForkProtocol` (block body, header, attestation data, etc.) are now typed as `type[SpecSSZType]`; the concrete narrowing happens in each fork's typed base class.

### `SpecSSZType`

The base protocol every consensus container satisfies.

| Member | Kind | Description |
| --- | --- | --- |
| `encode_bytes() -> bytes` | method | Serialize the container to SSZ bytes |
| `decode_bytes(data) -> Self` | classmethod | Deserialize SSZ bytes into a new container instance |

### `SpecStateType`

The consensus state container.

| Property | Type | Description |
| --- | --- | --- |
| `slot` | `Slot` | The current slot of this state |
| `config` | `SpecSSZType` | Genesis configuration carried by the state |

### `SpecBlockType`

A block container.

| Property | Type | Description |
| --- | --- | --- |
| `slot` | `Slot` | The slot at which the block was proposed |
| `proposer_index` | `ValidatorIndex` | The validator index of the proposer |
| `parent_root` | `Bytes32` | The SSZ root of the parent block |
| `state_root` | `Bytes32` | The SSZ root of the post-state produced by this block |

### `SpecStoreType`

The forkchoice store surface that sync, chain, and node services drive without depending on a concrete fork.

The protocol surface is **read-only**: only properties, no methods.
Concrete fork stores carry the mutation methods (`from_anchor`, `on_block`, `on_gossip_attestation`, ...) but those are not part of the cross-fork contract.

| Property | Type | Description |
| --- | --- | --- |
| `head` | `Bytes32` | Root of the canonical head block |
| `safe_target` | `Bytes32` | Root of the current safe target block |
| `latest_justified` | `Checkpoint` | Most recent justified checkpoint |
| `latest_finalized` | `Checkpoint` | Most recent finalized checkpoint |
| `validator_index` | `ValidatorIndex | None` | Index of the local validator owning this store, if any |
| `blocks` | `Mapping[Bytes32, SpecBlockType]` | Mapping from block root to known block |
| `states` | `Mapping[Bytes32, SpecStateType]` | Mapping from block root to post-state of that block |

## Nominal interface: `ForkProtocol`

`ForkProtocol` is the abstract base class every fork must extend.
A new fork is implemented by writing:

```python
class MyFork(ForkProtocol):
    # identity
    NAME: ClassVar[str] = "my_fork"
    VERSION: ClassVar[int] = 5
    GOSSIP_DIGEST: ClassVar[str] = "00000000"

    # concrete container class slots
    state_class = State
    block_class = Block
    block_body_class = BlockBody
    block_header_class = BlockHeader
    aggregated_attestations_class = AggregatedAttestations
    store_class = Store
    attestation_data_class = AttestationData
    aggregated_attestation_class = AggregatedAttestation
    genesis_config_class = GenesisConfig

    # abstract methods (create_store is on ForkProtocol;
    # generate_genesis is on the concrete fork's typed base — see below)
    def create_store(self, ...): ...
```

### Identity fields

| Field | Type | Description |
| --- | --- | --- |
| `NAME` | `str` | Unique fork name across the registry |
| `VERSION` | `int` | Strictly monotonic version used for registry ordering |
| `GOSSIP_DIGEST` | `str` | Fork identifier embedded in gossipsub topic names; must match the digest used by other clients on the same network for block, attestation, and aggregation topics to route compatibly |

### Container class slots

A fork wires nine concrete container classes into typed class-level attributes on `ForkProtocol`.
Two slots carry rich protocol types (`SpecStateType`, `SpecBlockType`); the other seven were collapsed to `SpecSSZType` in PR #882 because no caller ever reads them through the protocol abstraction — the structural typing they added was documentation pretending to be types.

| Slot | Protocol | Concrete container (lstar) |
| --- | --- | --- |
| `state_class` | `SpecStateType` | `State` |
| `block_class` | `SpecBlockType` | `Block` |
| `block_body_class` | `SpecSSZType` | `BlockBody` |
| `block_header_class` | `SpecSSZType` | `BlockHeader` |
| `aggregated_attestations_class` | `SpecSSZType` | `AggregatedAttestations` |
| `store_class` | `SpecStoreType` | `LstarStore` |
| `attestation_data_class` | `SpecSSZType` | `AttestationData` |
| `aggregated_attestation_class` | `SpecSSZType` | `AggregatedAttestation` |
| `genesis_config_class` | `SpecSSZType` | `GenesisConfig` |

Concrete fork base classes (e.g. `LstarSpecBase`) narrow these slots back to their real container types so production callers see the typed shape.
Signed envelopes (`SignedBlock`, `SignedAttestation`, `SignedAggregatedAttestation`) are not named in the protocol layer; concrete fork stores reference them directly through their own method signatures.

### Abstract methods

A fork must implement one lifecycle hook on `ForkProtocol` itself: `create_store`.
It is declared `@abstractmethod`; instantiating a fork that omits it raises `TypeError` at class-creation time.

Genesis construction is **not** on `ForkProtocol`. It used to be — declared as `generate_genesis(self, genesis_time: Uint64, validators: SSZList[Any]) -> SpecStateType` — but PR #1141 relocated the declaration to each fork's concrete typed base (for lstar: `LstarSpecBase`). The abstract signature had erased the validator element type to `SSZList[Any]`, and the concrete lstar override recovered it with an `assert isinstance(validators, Validators)` that vanishes under `python -O`. Genesis is only ever invoked on the concrete fork (the composition root narrows to `LstarSpec`; tests and the testing package instantiate it directly), never through `ForkProtocol`. Moving the declaration onto the concrete base lets it carry the real `Validators` and state types, and the recovering assert is gone. `ty` enforces parameter contravariance, so narrowing the override alone would break Liskov against the abstract protocol; relocating is what keeps the type checker happy.

#### `create_store`

```
def create_store(
    self,
    state: SpecStateType,
    anchor_block: SpecBlockType,
    validator_index: ValidatorIndex | None,
) -> SpecStoreType
```

Construct a forkchoice store anchored at the given state and block.
The anchor is either the genesis pair or a checkpoint-sync result.

> **Note (#1028):** lstar is the only fork, so there is no fork boundary to migrate across. The cross-fork `upgrade_state` hook and the `previous` fork-chaining classvar were speculative scaffolding with no callers — the registry orders forks by `VERSION`, not by a `previous` link, and nothing dispatches a state migration. Both were removed; they will be reintroduced alongside the second fork that actually needs a migration.

## Registry

The registry holds the ordered set of registered forks and provides lookups.

### `ForkRegistry`

```
class ForkRegistry:
    def __init__(self, forks: list[ForkProtocol]) -> None: ...

    @property
    def current(self) -> ForkProtocol: ...
```

The `get_fork(name)` lookup was removed in PR #883 (no production caller; name-uniqueness validation builds its own set).
Construction validates two invariants on the supplied fork list:

1. The list is non-empty.
2. `VERSION` is strictly monotonically increasing across the list (ascending).
3. `NAME` is unique across the list.

Violations raise `ValueError` at construction time.

`current` returns the highest-version fork (the last entry of the ordered list).

### `FORK_SEQUENCE` and `DEFAULT_REGISTRY`

The package exposes a shared registry over the currently registered forks:

```
FORK_SEQUENCE: list[ForkProtocol] = [LstarSpec()]
DEFAULT_REGISTRY: ForkRegistry = ForkRegistry(FORK_SEQUENCE)
```

Runtime callers access the active fork via `DEFAULT_REGISTRY.current`.
The `Store` symbol exported from `lean_spec.spec.forks` is a public alias resolving to the concrete `LstarStore` until other forks land; once additional forks register, callers should switch to `DEFAULT_REGISTRY.current.store_class` for fork-aware access.

## Adding a new fork

A new fork `lstar2` would land as follows.

1. Create `src/lean_spec/spec/forks/lstar2/` with concrete container classes and a `Lstar2Spec(ForkProtocol)` implementation.
2. Pick `VERSION` strictly greater than lstar's; assign a fresh `NAME` and `GOSSIP_DIGEST`.
3. Wire all nine `*_class` slots to the fork's concrete container classes.
4. Implement `generate_genesis` and `create_store`. Reintroduce a state-migration hook (e.g. `upgrade_state(state: LstarState) -> Lstar2State`) since lstar2 is the first fork that crosses a fork boundary; this is the machinery #1028 removed while lstar stood alone.
5. Add `Lstar2Spec()` to `FORK_SEQUENCE` in `forks/__init__.py`, preserving ascending version order.
6. Update wire-layer artifacts in `node/networking/`, `node/api/endpoints/`, `node/chain/config.py` to reflect the new fork's gossip topics, reqresp message types, API payloads, and tunable constants.
7. Add a fork builder under `packages/testing/consensus_testing/forks/lstar2/` so the filler can generate test vectors for the new fork.
8. Add `specs/lstar2/` to this repository documenting only what lstar2 changes relative to lstar.
