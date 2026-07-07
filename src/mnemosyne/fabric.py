"""MemoryFabric: the merge layer over the canonical (shared, read-only) and
overlay (private, read/write) tiers. This is the single entry point the MCP
server calls. All writes route to the overlay — there is no code path from
the fabric to a canonical write."""

from __future__ import annotations

from typing import Any, Literal

from mnemosyne.matching import Matcher
from mnemosyne.models import (
    USER_PREFIX,
    Alias,
    Entity,
    GraphEdge,
    Memory,
    MergeProposal,
    Provenance,
    Relationship,
    ResolutionResult,
    ResolvedEntity,
    Tier,
    new_id,
    tier_of_id,
    utcnow,
)
from mnemosyne.ontology import OntologyRegistry
from mnemosyne.resolution import IdentityResolver, normalize
from mnemosyne.storage.base import CanonicalStore
from mnemosyne.storage.sqlite_overlay import SqliteOverlayStore
from mnemosyne.temporal import build_timeline, is_current, is_valid_at

DEFAULT_CREATED_CONFIDENCE = 0.7


class FabricError(ValueError):
    """User-visible fabric error (unknown entity, bad reference, etc.)."""


class MemoryFabric:
    def __init__(
        self,
        canonical: CanonicalStore,
        overlay: SqliteOverlayStore,
        ontology: OntologyRegistry | None = None,
        matcher: Matcher | None = None,
    ):
        self.canonical = canonical
        self.overlay = overlay
        self.ontology = ontology or OntologyRegistry.load()
        self.resolver = IdentityResolver(canonical, overlay, self.ontology, matcher)

    # ------------------------------------------------------------------
    # entity views
    # ------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> tuple[Entity, Tier] | None:
        """Merged view of an entity. Overlay `extends_id` rows deep-merge over
        their canonical base (overlay attributes win key-by-key); canonical
        entities are checked for an overlay enrichment row."""
        home_tier = tier_of_id(entity_id)
        if home_tier == "overlay":
            entity = self.overlay.get_entity(entity_id)
            if entity is None:
                return None
            if entity.extends_id:
                base = self.canonical.get_entity(entity.extends_id)
                if base is not None:
                    return self._merge_entities(base, entity), "merged"
            return entity, "overlay"

        entity = self.canonical.get_entity(entity_id)
        if entity is None:
            return None
        extension = self.overlay.entity_extending(entity_id)
        if extension is not None:
            return self._merge_entities(entity, extension), "merged"
        return entity, "canonical"

    @staticmethod
    def _merge_entities(base: Entity, extension: Entity) -> Entity:
        merged = base.model_copy(deep=True)
        merged.attributes = {**base.attributes, **extension.attributes}
        if extension.summary:
            merged.summary = extension.summary
        return merged

    def aliases_of(self, entity_id: str) -> list[str]:
        """Alias strings from both tiers (union), plus any extension row's."""
        alias_rows = list(self.overlay.aliases_for(entity_id))
        if tier_of_id(entity_id) == "canonical":
            alias_rows.extend(self.canonical.aliases_for(entity_id))
            extension = self.overlay.entity_extending(entity_id)
            if extension is not None:
                alias_rows.extend(self.overlay.aliases_for(extension.id))
        else:
            entity = self.overlay.get_entity(entity_id)
            if entity and entity.extends_id:
                alias_rows.extend(self.canonical.aliases_for(entity.extends_id))
                alias_rows.extend(self.overlay.aliases_for(entity.extends_id))
        return sorted({a.alias for a in alias_rows})

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------

    def resolve_entity(
        self,
        name: str,
        provenance: Provenance,
        entity_type: str | None = None,
        create_if_missing: bool = False,
    ) -> ResolutionResult:
        if entity_type is not None:
            self.ontology.validate_entity_type(entity_type)
        outcome = self.resolver.resolve(name, entity_type)

        if outcome.status == "matched":
            entity, tier = self.get_entity(outcome.entity.id) or (outcome.entity, "overlay")
            return ResolutionResult(
                status="matched",
                entity=entity,
                tier=tier,
                method=outcome.method,  # type: ignore[arg-type]
                score=outcome.score,
            )

        if outcome.status == "auto_matched":
            # High-confidence fuzzy hit: accept AND learn the mention as a new
            # alias (in the overlay, whichever tier the entity lives in) so the
            # system recognizes this spelling instantly next time.
            self.overlay.add_alias(
                Alias(
                    entity_id=outcome.entity.id,
                    alias=name,
                    normalized_alias=normalize(name),
                    confidence=outcome.score / 100.0,
                    provenance_id=provenance.id,
                ),
                provenance,
            )
            entity, tier = self.get_entity(outcome.entity.id) or (outcome.entity, "overlay")
            return ResolutionResult(
                status="matched", entity=entity, tier=tier, method="fuzzy", score=outcome.score
            )

        if outcome.status == "proposed":
            # Mid-confidence: propose, don't autocommit. The caller decides
            # whether to fall back to a provisional entity (see resolve_or_create).
            proposal = self.overlay.create_proposal(
                MergeProposal(
                    candidate_name=name,
                    entity_id=outcome.entity.id,
                    score=outcome.score,
                    provenance_id=provenance.id,
                ),
                provenance,
            )
            return ResolutionResult(
                status="proposed",
                entity=outcome.entity,
                tier=tier_of_id(outcome.entity.id),
                method="fuzzy",
                score=outcome.score,
                proposal_id=proposal.id,
            )

        # not_found
        if create_if_missing:
            entity = self.create_entity(
                name=name,
                entity_type=entity_type or "concept",
                provenance=provenance,
                confidence=DEFAULT_CREATED_CONFIDENCE,
            )
            return ResolutionResult(
                status="created", entity=entity, tier="overlay", method="created", score=100.0
            )
        return ResolutionResult(
            status="not_found",
            near_misses=[
                {"name": c.entity.name, "id": c.entity.id, "score": round(c.score, 1)}
                for c in (outcome.near_misses or [])
            ],
        )

    def resolve_or_create(
        self,
        name: str,
        provenance: Provenance,
        entity_type: str | None = None,
    ) -> tuple[ResolutionResult, Entity]:
        """Resolution used by remember/assert_relationship: the write must
        always land somewhere. Proposed matches keep the proposal on record but
        create a provisional overlay entity rather than silently linking."""
        result = self.resolve_entity(name, provenance, entity_type, create_if_missing=True)
        if result.status == "proposed":
            provisional = self.create_entity(
                name=name,
                entity_type=entity_type or "concept",
                provenance=provenance.model_copy(
                    update={
                        "derivation": {
                            **(provenance.derivation or {}),
                            "pending_proposal": result.proposal_id,
                            "proposed_match": result.entity.id if result.entity else None,
                        }
                    }
                ),
                confidence=DEFAULT_CREATED_CONFIDENCE,
            )
            return result, provisional
        assert result.entity is not None
        return result, result.entity

    # ------------------------------------------------------------------
    # writes (overlay only)
    # ------------------------------------------------------------------

    def create_entity(
        self,
        name: str,
        entity_type: str,
        provenance: Provenance,
        aliases: list[str] | None = None,
        summary: str | None = None,
        attributes: dict[str, Any] | None = None,
        extends: str | None = None,
        confidence: float = 0.9,
        valid_from: str | None = None,
    ) -> Entity:
        self.ontology.validate_entity_type(entity_type)
        if extends is not None and self.canonical.get_entity(extends) is None:
            raise FabricError(f"extends target {extends!r} not found in the canonical tier")
        entity = Entity(
            id=new_id(USER_PREFIX),
            entity_type=entity_type,
            name=name,
            normalized_name=normalize(name),
            summary=summary,
            attributes=attributes or {},
            confidence=confidence,
            extends_id=extends,
            valid_from=valid_from or utcnow(),
            provenance_id=provenance.id,
        )
        self.overlay.create_entity(entity, provenance)
        for alias in aliases or []:
            self.overlay.add_alias(
                Alias(
                    entity_id=entity.id,
                    alias=alias,
                    normalized_alias=normalize(alias),
                    provenance_id=provenance.id,
                ),
                provenance,
            )
        return entity

    def remember(
        self,
        content: str,
        provenance: Provenance,
        mentions: list[str] | None = None,
        kind: str = "fact",
        confidence: float = 0.8,
        valid_from: str | None = None,
    ) -> dict[str, Any]:
        linked: list[ResolvedEntity] = []
        proposals: list[str] = []
        entity_ids: list[str] = []
        for mention in mentions or []:
            result, entity = self.resolve_or_create(mention, provenance)
            entity_ids.append(entity.id)
            if result.proposal_id:
                proposals.append(result.proposal_id)
            linked.append(
                ResolvedEntity(
                    entity=entity,
                    tier=tier_of_id(entity.id),
                    method=result.method or "created",
                    score=result.score or 100.0,
                    aliases=self.aliases_of(entity.id),
                )
            )
        memory = Memory(
            content=content,
            memory_kind=kind,  # type: ignore[arg-type]
            confidence=confidence,
            valid_from=valid_from or utcnow(),
            provenance_id=provenance.id,
            entity_ids=entity_ids,
        )
        self.overlay.record_memory(memory, provenance)
        return {"memory": memory, "linked_entities": linked, "proposal_ids": proposals}

    def assert_relationship(
        self,
        source: str,
        target: str,
        rel_type: str,
        provenance: Provenance,
        confidence: float = 0.8,
        valid_from: str | None = None,
        attributes: dict[str, Any] | None = None,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        # Missing endpoints are auto-created; if the relationship type pins the
        # endpoint to a single entity type, use it as the creation hint.
        rel_def = self.ontology.relationship_types.get(rel_type)
        if rel_def is None:
            raise FabricError(
                f"Unknown relationship type {rel_type!r}. "
                f"Valid types: {sorted(self.ontology.relationship_types)}"
            )
        source_hint = rel_def.source_types[0] if len(rel_def.source_types) == 1 else None
        target_hint = rel_def.target_types[0] if len(rel_def.target_types) == 1 else None
        source_result, source_entity = self.resolve_or_create(
            source, provenance, None if source_hint in (None, "*") else source_hint
        )
        target_result, target_entity = self.resolve_or_create(
            target, provenance, None if target_hint in (None, "*") else target_hint
        )
        self.ontology.validate_relationship(
            rel_type, source_entity.entity_type, target_entity.entity_type
        )

        relationship = Relationship(
            source_entity_id=source_entity.id,
            target_entity_id=target_entity.id,
            rel_type=rel_type,
            attributes=attributes or {},
            confidence=confidence,
            valid_from=valid_from or utcnow(),
            provenance_id=provenance.id,
        )

        superseded_ids: list[str] = []
        to_supersede: list[str] = []
        if supersedes:
            to_supersede.append(supersedes)
        elif self.ontology.is_functional(rel_type):
            # Functional relationship (e.g. works_at): a new assertion from the
            # same source closes any open edge of the same type.
            to_supersede.extend(
                r.id
                for r in self.overlay.relationships_for(source_entity.id)
                if r.rel_type == rel_type
                and r.source_entity_id == source_entity.id
                and r.id != relationship.id
                and is_current(r)
            )

        if to_supersede:
            first, *rest = to_supersede
            self.overlay.supersede_relationship(first, relationship, provenance)
            superseded_ids.append(first)
            for old_id in rest:
                self.overlay.close_relationship(
                    old_id, relationship.id, relationship.valid_from, provenance
                )
                superseded_ids.append(old_id)
        else:
            self.overlay.assert_relationship(relationship, provenance)

        return {
            "relationship": relationship,
            "source": source_result,
            "target": target_result,
            "superseded": superseded_ids,
            "proposal_ids": [
                p for p in (source_result.proposal_id, target_result.proposal_id) if p
            ],
        }

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def query_graph(
        self,
        entity_id: str,
        rel_type: str | None = None,
        direction: Literal["out", "in", "both"] = "both",
        depth: int = 1,
        as_of: str | None = None,
        include_superseded: bool = False,
    ) -> list[GraphEdge]:
        """BFS over the merged two-tier graph. Overlay edges may cross into the
        canonical tier; every edge is tagged with the tier it lives in."""
        depth = max(1, min(depth, 2))
        edges: dict[str, GraphEdge] = {}
        frontier = {entity_id}
        seen_nodes: set[str] = set()

        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                if node in seen_nodes:
                    continue
                seen_nodes.add(node)
                for tier, store in (("overlay", self.overlay), ("canonical", self.canonical)):
                    for rel in store.relationships_for(node):
                        if rel.id in edges:
                            continue
                        if rel_type and rel.rel_type != rel_type:
                            continue
                        if direction == "out" and rel.source_entity_id != node:
                            continue
                        if direction == "in" and rel.target_entity_id != node:
                            continue
                        if as_of is not None:
                            if not is_valid_at(rel, as_of):
                                continue
                        elif not include_superseded and not is_current(rel):
                            continue
                        edges[rel.id] = self._edge_view(rel, tier)  # type: ignore[arg-type]
                        next_frontier.add(rel.source_entity_id)
                        next_frontier.add(rel.target_entity_id)
            frontier = next_frontier - seen_nodes
        return list(edges.values())

    def _edge_view(self, rel: Relationship, tier: Tier) -> GraphEdge:
        prov = None
        store = self.overlay if tier == "overlay" else self.canonical
        prov = store.get_provenance(rel.provenance_id)
        return GraphEdge(
            relationship=rel,
            source_name=self._name_of(rel.source_entity_id),
            target_name=self._name_of(rel.target_entity_id),
            tier=tier,
            provenance_summary={
                "source_type": prov.source_type if prov else None,
                "assistant_id": prov.assistant_id if prov else None,
                "recorded_at": prov.recorded_at if prov else None,
            },
        )

    def _name_of(self, entity_id: str) -> str:
        found = self.get_entity(entity_id)
        return found[0].name if found else entity_id

    def recall(
        self,
        query: str,
        provenance: Provenance,
        entity: str | None = None,
        kind: str | None = None,
        as_of: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        resolved: ResolutionResult | None = None
        memories: dict[str, Memory] = {}
        if query:
            for m in self.overlay.search_memories(query, limit=limit):
                memories[m.id] = m
        if entity:
            resolved = self.resolve_entity(entity, provenance)
            if resolved.entity is not None:
                for m in self.overlay.memories_for_entity(resolved.entity.id, limit=limit):
                    memories[m.id] = m

        results = []
        for m in memories.values():
            if kind and m.memory_kind != kind:
                continue
            if as_of is not None and not is_valid_at(m, as_of):
                continue
            prov = self.overlay.get_provenance(m.provenance_id)
            results.append(
                {
                    "memory": m,
                    "entities": [self._name_of(eid) for eid in m.entity_ids],
                    "superseded": m.superseded_by is not None,
                    "provenance": {
                        "source_type": prov.source_type if prov else None,
                        "assistant_id": prov.assistant_id if prov else None,
                        "recorded_at": prov.recorded_at if prov else None,
                    },
                }
            )
        results.sort(key=lambda r: r["memory"].created_at, reverse=True)
        return {"memories": results[:limit], "resolved_entity": resolved}

    def entity_timeline(self, entity_id: str) -> list:
        """Full append-only history for an entity and its relationships, drawn
        from both tiers (seed events included)."""
        assertions = []
        prov_lookup = {}
        for store in (self.overlay, self.canonical):
            subject_ids = {entity_id}
            subject_ids.update(r.id for r in store.relationships_for(entity_id))
            subject_ids.update(a.id for a in store.aliases_for(entity_id))
            for sid in subject_ids:
                assertions.extend(store.assertions_for(sid))
            for a in assertions:
                if a.provenance_id not in prov_lookup:
                    prov = store.get_provenance(a.provenance_id)
                    if prov:
                        prov_lookup[a.provenance_id] = prov
        return build_timeline(assertions, prov_lookup)
