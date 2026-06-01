---
last_synced_commit: 87943be
source_files:
  - src/lean_spec/spec/forks/__init__.py
  - src/lean_spec/spec/forks/protocol.py
  - src/lean_spec/spec/forks/registry.py
related_prs: [638, 686]
---

# Fork Protocol

<!-- TOC -->

- [Introduction](#introduction)
- [Two layers of typing](#two-layers-of-typing)
- [Structural protocols](#structural-protocols)
  - [`SpecSSZType`](#specssz-type)
  - [`SpecConfigType`](#specconfigtype)
  - [`SpecStateType`](#specstatetype)
  - [`SpecBlockType`](#specblocktype)
  - [`SpecBlockBodyType`](#specblockbodytype)
  - [`SpecBlockHeaderType`](#specblockheadertype)
  - [`SpecAggregatedAttestationsType`](#specaggregatedattestationstype)
  - [`SpecSignedBlockType`](#specsignedblocktype)
  - [`SpecAttestationDataType`](#specattestationdatatype)
  - [`SpecSignedAttestationType`](#specsignedattestationtype)
  - [`SpecAggregatedAttestationType`](#specaggregatedattestationtype)
  - [`SpecSignedAggregatedAttestationType`](#specsignedaggregatedattestationtype)
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
A fork is a versioned implementation that supplies a complete set of consensus container classes plus three lifecycle hooks: genesis construction, store construction, and state migration from the predecessor fork.

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

Every structural protocol below extends `SpecSSZType`, so every fork container is required to expose SSZ encode and decode methods at minimum.
Specific protocols add further required properties.

### `SpecSSZType`

The base protocol every consensus container satisfies.

| Member | Kind | Description |
| --- | --- | --- |
| `encode_bytes() -> bytes` | method | Serialize the container to SSZ bytes |
| `decode_bytes(data) -> Self` | classmethod | Deserialize SSZ bytes into a new container instance |

### `SpecConfigType`

The genesis configuration container exposed by a fork.
Extends `SpecSSZType` and adds no further members; the marker exists to constrain typing at fork boundaries.

### `SpecStateType`

The consensus state container.

| Property | Type | Description |
| --- | --- | --- |
| `slot` | `Slot` | The current slot of this state |
| `config` | `SpecConfigType` | Genesis configuration carried by the state |

### `SpecBlockType`

A block container.

| Property | Type | Description |
| --- | --- | --- |
| `slot` | `Slot` | The slot at which the block was proposed |
| `proposer_index` | `ValidatorIndex` | The validator index of the proposer |
| `parent_root` | `Bytes32` | The SSZ root of the parent block |
| `state_root` | `Bytes32` | The SSZ root of the post-state produced by this block |

### `SpecBlockBodyType`

A block body. Extends `SpecSSZType` and adds no further members.
The variable-size payload attached to a block.
Holds aggregated attestations and any future operation lists.

### `SpecBlockHeaderType`

A block header. Extends `SpecSSZType` and adds no further members.
The fixed-shape summary of a block used in state-transition tracking and state-root caching.
Carries the proposer, parent root, state root, and body root.

### `SpecAggregatedAttestationsType`

A bounded SSZ list of aggregated attestations included in a block body.
Extends `SpecSSZType` and adds no further members.

### `SpecSignedBlockType`

A signed-block envelope wrapping a Block plus its aggregated proof of every attestation in the body and the proposer's signature over the block root.

| Property | Type | Description |
| --- | --- | --- |
| `block` | `SpecBlockType` | The wrapped block |

Sync, gossip, and storage treat instances as opaque SSZ payloads passed between services.

### `SpecAttestationDataType`

A validator's view of the chain at the point of signing.

| Property | Type | Description |
| --- | --- | --- |
| `slot` | `Slot` | The slot the attestation is voting at |
| `head` | `Checkpoint` | The head checkpoint the attestation votes for |
| `source` | `Checkpoint` | The source checkpoint of the attestation |
| `target` | `Checkpoint` | The target checkpoint of the attestation |

### `SpecSignedAttestationType`

A single validator's attestation bundled with its individual signature.

| Property | Type | Description |
| --- | --- | --- |
| `data` | `SpecAttestationDataType` | The unsigned attestation payload |
| `validator_id` | `ValidatorIndex` | The validator that produced this attestation |

### `SpecAggregatedAttestationType`

An attestation aggregated over multiple validators via a participation bitfield.

| Property | Type | Description |
| --- | --- | --- |
| `data` | `SpecAttestationDataType` | The unsigned attestation payload |

The aggregation bitfield itself is consensus-visible but not required by the protocol shape; the concrete container exposes it directly.

### `SpecSignedAggregatedAttestationType`

The aggregator's broadcast payload, combining attestation data with the aggregated signature proof.

| Property | Type | Description |
| --- | --- | --- |
| `data` | `SpecAttestationDataType` | The unsigned attestation payload |

### `SpecStoreType`

The forkchoice store surface that sync, chain, and node services drive without depending on a concrete fork.

#### Required properties

| Property | Type | Description |
| --- | --- | --- |
| `head` | `Bytes32` | Root of the canonical head block |
| `safe_target` | `Bytes32` | Root of the current safe target block |
| `latest_justified` | `Checkpoint` | Most recent justified checkpoint |
| `latest_finalized` | `Checkpoint` | Most recent finalized checkpoint |
| `validator_id` | `ValidatorIndex | None` | Index of the local validator owning this store, if any |
| `blocks` | `Mapping[Bytes32, SpecBlockType]` | Mapping from block root to known block |
| `states` | `Mapping[Bytes32, SpecStateType]` | Mapping from block root to post-state of that block |

#### Required methods

| Method | Description |
| --- | --- |
| `from_anchor(state, anchor_block, validator_id) -> Self` | Construct a forkchoice store anchored at the given state and block |
| `on_block(signed_block) -> Self` | Apply a signed block to the store and return the updated store |
| `on_gossip_attestation(signed_attestation, is_aggregator) -> Self` | Apply a single-validator attestation and return the updated store |
| `on_gossip_aggregated_attestation(signed_attestation) -> Self` | Apply an aggregated attestation and return the updated store |

The implementing fork is free to add further methods.
Only the listed surface is part of the cross-fork contract.

## Nominal interface: `ForkProtocol`

`ForkProtocol` is the abstract base class every fork must extend.
A new fork is implemented by writing:

```python
class MyFork(ForkProtocol):
    # identity
    NAME: ClassVar[str] = "my_fork"
    VERSION: ClassVar[int] = 5
    GOSSIP_DIGEST: ClassVar[str] = "00000000"
    previous: ClassVar[type[ForkProtocol] | None] = PreviousFork

    # concrete container class slots
    state_class = State
    block_class = Block
    block_body_class = BlockBody
    block_header_class = BlockHeader
    aggregated_attestations_class = AggregatedAttestations
    store_class = Store
    attestation_data_class = AttestationData
    aggregated_attestation_class = AggregatedAttestation
    config_class = Config

    # abstract methods
    def generate_genesis(self, ...): ...
    def create_store(self, ...): ...
    def upgrade_state(self, ...): ...
```

### Identity fields

| Field | Type | Description |
| --- | --- | --- |
| `NAME` | `str` | Unique fork name across the registry |
| `VERSION` | `int` | Strictly monotonic version used for registry ordering |
| `GOSSIP_DIGEST` | `str` | Fork identifier embedded in gossipsub topic names; must match the digest used by other clients on the same network for block, attestation, and aggregation topics to route compatibly |
| `previous` | `type[ForkProtocol] | None` | Predecessor fork in the upgrade chain, or `None` for the root fork; forms a linked chain that the registry walks to derive ordering and that `upgrade_state` traverses for cross-fork state migrations |

### Container class slots

A fork wires nine concrete container classes into typed class-level attributes.
Each slot is typed by a structural protocol from the previous section; the assigned class must satisfy that protocol.

| Slot | Protocol | Concrete container (lstar) |
| --- | --- | --- |
| `state_class` | `SpecStateType` | `State` |
| `block_class` | `SpecBlockType` | `Block` |
| `block_body_class` | `SpecBlockBodyType` | `BlockBody` |
| `block_header_class` | `SpecBlockHeaderType` | `BlockHeader` |
| `aggregated_attestations_class` | `SpecAggregatedAttestationsType` | `AggregatedAttestations` |
| `store_class` | `SpecStoreType` | `LstarStore` |
| `attestation_data_class` | `SpecAttestationDataType` | `AttestationData` |
| `aggregated_attestation_class` | `SpecAggregatedAttestationType` | `AggregatedAttestation` |
| `config_class` | `SpecConfigType` | `Config` |

Additional Spec*Type protocols (`SpecSignedBlockType`, `SpecSignedAttestationType`, `SpecSignedAggregatedAttestationType`) are referenced by Store method signatures but not held in dedicated slots.
A fork still defines concrete classes for them; they reach the runtime through method signatures on the Store.

### Abstract methods

A fork must implement three lifecycle hooks.
All three are declared `@abstractmethod` on `ForkProtocol`; instantiating a fork that omits any of them raises `TypeError` at class-creation time.

#### `generate_genesis`

```
def generate_genesis(self, genesis_time: Uint64, validators: SSZList[Any]) -> SpecStateType
```

Construct a genesis state for this fork.

- Inputs: genesis time and the ordered validator list.
- Output: an instance of the fork's state class with all fields initialized to their genesis values.

#### `create_store`

```
def create_store(
    self,
    state: SpecStateType,
    anchor_block: SpecBlockType,
    validator_id: ValidatorIndex | None,
) -> SpecStoreType
```

Construct a forkchoice store anchored at the given state and block.
The anchor is either the genesis pair or a checkpoint-sync result.

#### `upgrade_state`

```
def upgrade_state(self, state: SpecStateType) -> SpecStateType
```

Migrate a state object from the predecessor fork's shape into this fork's shape.

- The root fork (`previous = None`) returns the input unchanged.
- Later forks return a state of their own shape derived from the predecessor's.

Making this method abstract is intentional.
A silent no-op default would hide missed migrations whenever a fork adds a field but forgets to override.

## Registry

The registry holds the ordered set of registered forks and provides lookups.

### `ForkRegistry`

```
class ForkRegistry:
    def __init__(self, forks: list[ForkProtocol]) -> None: ...

    @property
    def current(self) -> ForkProtocol: ...

    def get_fork(self, name: str) -> ForkProtocol: ...
```

Construction validates two invariants on the supplied fork list:

1. The list is non-empty.
2. `VERSION` is strictly monotonically increasing across the list (ascending).
3. `NAME` is unique across the list.

Violations raise `ValueError` at construction time.

`current` returns the highest-version fork (the last entry of the ordered list).
`get_fork(name)` looks up a fork by `NAME`; an unknown name raises `KeyError` with the sorted list of known names included in the message.

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
2. Set `previous = LstarSpec` on `Lstar2Spec`; pick `VERSION` strictly greater than lstar's; assign a fresh `NAME` and `GOSSIP_DIGEST`.
3. Wire all nine `*_class` slots to the fork's concrete container classes.
4. Implement `generate_genesis`, `create_store`, and `upgrade_state(state: LstarState) -> Lstar2State`.
5. Add `Lstar2Spec()` to `FORK_SEQUENCE` in `forks/__init__.py`, preserving ascending version order.
6. Update wire-layer artifacts in `node/networking/`, `node/api/endpoints/`, `node/chain/config.py` to reflect the new fork's gossip topics, reqresp message types, API payloads, and tunable constants.
7. Add a fork builder under `packages/testing/consensus_testing/forks/lstar2/` so the filler can generate test vectors for the new fork.
8. Add `specs/lstar2/` to this repository documenting only what lstar2 changes relative to lstar.
