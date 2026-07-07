"""Store interfaces. The canonical tier exposes only reads; all writes are
overlay-only, so private data structurally cannot reach the shared tier."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mnemosyne.models import (
    Alias,
    Assertion,
    Entity,
    Memory,
    MergeProposal,
    Provenance,
    Relationship,
)


@runtime_checkable
class CanonicalStore(Protocol):
    """Read-only view of the shared/universal ontology tier. A hosted
    (e.g. Postgres) implementation swaps in behind this same interface."""

    def get_entity(self, entity_id: str) -> Entity | None: ...

    def find_by_normalized_name(self, normalized_name: str) -> list[Entity]: ...

    def find_aliases(self, normalized_alias: str) -> list[Alias]: ...

    def aliases_for(self, entity_id: str) -> list[Alias]: ...

    def all_aliases(self) -> list[Alias]: ...

    def all_entities(self, entity_type: str | None = None) -> list[Entity]: ...

    def relationships_for(self, entity_id: str) -> list[Relationship]: ...

    def get_provenance(self, provenance_id: str) -> Provenance | None: ...

    def assertions_for(self, subject_id: str) -> list[Assertion]: ...


@runtime_checkable
class OverlayStore(CanonicalStore, Protocol):
    """Read/write private tier. Every write takes a Provenance and appends the
    corresponding assertion row in the same transaction."""

    def save_provenance(self, provenance: Provenance) -> None: ...

    def create_entity(self, entity: Entity, provenance: Provenance) -> Entity: ...

    def add_alias(self, alias: Alias, provenance: Provenance) -> Alias: ...

    def assert_relationship(
        self, relationship: Relationship, provenance: Provenance
    ) -> Relationship: ...

    def supersede_relationship(
        self, old_id: str, new_relationship: Relationship, provenance: Provenance
    ) -> Relationship: ...

    def record_memory(self, memory: Memory, provenance: Provenance) -> Memory: ...

    def get_memory(self, memory_id: str) -> Memory | None: ...

    def search_memories(self, query: str, limit: int = 10) -> list[Memory]: ...

    def memories_for_entity(self, entity_id: str, limit: int = 50) -> list[Memory]: ...

    def create_proposal(self, proposal: MergeProposal, provenance: Provenance) -> MergeProposal: ...

    def list_proposals(self, status: str | None = "pending") -> list[MergeProposal]: ...

    def get_proposal(self, proposal_id: str) -> MergeProposal | None: ...

    def resolve_proposal(
        self, proposal_id: str, status: str, provenance: Provenance
    ) -> MergeProposal: ...
