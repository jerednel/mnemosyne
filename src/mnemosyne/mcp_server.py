"""Mnemosyne MCP server: exposes the memory fabric to assistants over stdio.

Every write tool captures provenance server-side: the assistant is identified
from the MCP initialize handshake (clientInfo), timestamps are server-set, and
the assistant's stated confidence is recorded verbatim."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from mnemosyne.config import fallback_assistant_id, overlay_db_path
from mnemosyne.fabric import MemoryFabric
from mnemosyne.models import Provenance
from mnemosyne.ontology import OntologyRegistry
from mnemosyne.storage import canonical_store_from_env
from mnemosyne.storage.sqlite_overlay import SqliteOverlayStore

mcp = FastMCP("mnemosyne")

_fabric: MemoryFabric | None = None
_session_ids: dict[int, str] = {}


def get_fabric() -> MemoryFabric:
    global _fabric
    if _fabric is None:
        from mnemosyne.matching import matcher_from_env

        _fabric = MemoryFabric(
            canonical=canonical_store_from_env(),
            overlay=SqliteOverlayStore(overlay_db_path()),
            ontology=OntologyRegistry.load(),
            matcher=matcher_from_env(),
        )
    return _fabric


def provenance_from_context(
    ctx: Context,
    stated_confidence: float | None = None,
    raw_context: str | None = None,
) -> Provenance:
    assistant_id = fallback_assistant_id()
    try:
        params = ctx.session.client_params
        if params is not None and params.clientInfo is not None:
            assistant_id = f"{params.clientInfo.name}/{params.clientInfo.version}"
    except (AttributeError, ValueError):
        pass
    session_key = id(ctx.session)
    session_id = _session_ids.setdefault(session_key, uuid.uuid4().hex)
    return Provenance(
        source_type="assistant",
        assistant_id=assistant_id,
        session_id=session_id,
        stated_confidence=stated_confidence,
        raw_context=raw_context,
    )


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


@mcp.tool()
def remember(
    content: str,
    ctx: Context,
    entities: list[str] | None = None,
    kind: Literal["fact", "preference", "event", "note"] = "fact",
    confidence: float = 0.8,
    valid_from: str | None = None,
    source_context: str | None = None,
) -> dict:
    """Record a memory (a free-text observation) and link it to the entities it
    mentions. Pass entity mentions as they appeared ("DBX", "Jeremy") — they are
    resolved to canonical or private entities automatically."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx, confidence, source_context)
    result = fabric.remember(
        content,
        provenance,
        mentions=entities,
        kind=kind,
        confidence=confidence,
        valid_from=valid_from,
    )
    return _dump(
        {
            "memory_id": result["memory"].id,
            "linked_entities": result["linked_entities"],
            "proposal_ids": result["proposal_ids"],
        }
    )


@mcp.tool()
def recall(
    query: str,
    ctx: Context,
    entity: str | None = None,
    kind: Literal["fact", "preference", "event", "note"] | None = None,
    as_of: str | None = None,
    limit: int = 10,
) -> dict:
    """Search memories by text and/or by linked entity. `as_of` (ISO-8601)
    filters to facts valid at that instant; superseded memories are flagged."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx)
    return _dump(
        fabric.recall(query, provenance, entity=entity, kind=kind, as_of=as_of, limit=limit)
    )


@mcp.tool()
def resolve_entity(
    name: str,
    ctx: Context,
    entity_type: str | None = None,
    create_if_missing: bool = False,
) -> dict:
    """Resolve a mention ("DBX", "the lakehouse vendor") to a canonical or
    private entity. Returns status matched|proposed|created|not_found with the
    match method and confidence score."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx)
    return _dump(fabric.resolve_entity(name, provenance, entity_type, create_if_missing))


@mcp.tool()
def create_entity(
    name: str,
    entity_type: str,
    ctx: Context,
    aliases: list[str] | None = None,
    summary: str | None = None,
    attributes: dict | None = None,
    extends: str | None = None,
    confidence: float = 0.9,
) -> dict:
    """Create a private entity in the user's overlay. Pass `extends` with a
    canonical entity id (e.g. canon:company/databricks) to privately enrich a
    shared entity instead of duplicating it."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx, confidence)
    entity = fabric.create_entity(
        name=name,
        entity_type=entity_type,
        provenance=provenance,
        aliases=aliases,
        summary=summary,
        attributes=attributes,
        extends=extends,
        confidence=confidence,
    )
    return _dump(entity)


@mcp.tool()
def assert_relationship(
    source: str,
    target: str,
    rel_type: str,
    ctx: Context,
    confidence: float = 0.8,
    valid_from: str | None = None,
    attributes: dict | None = None,
    supersedes: str | None = None,
    source_context: str | None = None,
) -> dict:
    """Assert a typed relationship between two entities (mentions are resolved,
    missing endpoints auto-created). Functional relationship types like works_at
    automatically supersede the previous open fact instead of contradicting it.
    Use list_ontology to see valid relationship types."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx, confidence, source_context)
    result = fabric.assert_relationship(
        source,
        target,
        rel_type,
        provenance,
        confidence=confidence,
        valid_from=valid_from,
        attributes=attributes,
        supersedes=supersedes,
    )
    return _dump(result)


@mcp.tool()
def query_graph(
    entity: str,
    ctx: Context,
    rel_type: str | None = None,
    direction: Literal["out", "in", "both"] = "both",
    depth: int = 1,
    as_of: str | None = None,
    include_superseded: bool = False,
) -> dict:
    """Query the merged two-tier relationship graph around an entity. Edges are
    tagged with their tier (canonical = shared ontology, overlay = private).
    `as_of` answers "what was true at time T"."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx)
    resolved = fabric.resolve_entity(entity, provenance)
    if resolved.entity is None:
        return _dump({"root": resolved, "edges": []})
    edges = fabric.query_graph(
        resolved.entity.id,
        rel_type=rel_type,
        direction=direction,
        depth=depth,
        as_of=as_of,
        include_superseded=include_superseded,
    )
    return _dump({"root": resolved, "edges": edges})


@mcp.tool()
def get_entity_timeline(entity: str, ctx: Context) -> dict:
    """Full append-only history of an entity: creation, aliases, relationship
    assertions and supersessions, each with provenance (who asserted it, when,
    with what confidence)."""
    fabric = get_fabric()
    provenance = provenance_from_context(ctx)
    resolved = fabric.resolve_entity(entity, provenance)
    if resolved.entity is None:
        return _dump({"entity": resolved, "events": []})
    events = fabric.entity_timeline(resolved.entity.id)
    return _dump({"entity": resolved.entity, "events": events})


@mcp.tool()
def list_ontology() -> dict:
    """List the registered entity types and relationship taxonomy (with
    source/target constraints and functional flags). Use these exact type names
    when creating entities or asserting relationships."""
    return get_fabric().ontology.describe()


@mcp.tool()
def review_proposals(
    ctx: Context,
    action: Literal["list", "accept", "reject"] = "list",
    proposal_id: str | None = None,
) -> dict:
    """Review pending identity-merge proposals (mid-confidence matches that were
    recorded but not auto-linked). Accepting one adds the candidate mention as
    an alias of the proposed entity."""
    fabric = get_fabric()
    if action == "list":
        return _dump({"proposals": fabric.overlay.list_proposals("pending")})
    if proposal_id is None:
        raise ValueError("proposal_id is required for accept/reject")
    proposal = fabric.overlay.get_proposal(proposal_id)
    if proposal is None:
        raise ValueError(f"Proposal {proposal_id!r} not found")
    provenance = provenance_from_context(ctx)
    resolved = fabric.overlay.resolve_proposal(
        proposal_id, "accepted" if action == "accept" else "rejected", provenance
    )
    if action == "accept":
        from mnemosyne.models import Alias
        from mnemosyne.resolution import normalize

        fabric.overlay.add_alias(
            Alias(
                entity_id=proposal.entity_id,
                alias=proposal.candidate_name,
                normalized_alias=normalize(proposal.candidate_name),
                confidence=proposal.score / 100.0,
                provenance_id=provenance.id,
            ),
            provenance,
        )
    return _dump({"proposal": resolved})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
