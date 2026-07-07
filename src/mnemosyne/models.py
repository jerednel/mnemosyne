"""Pydantic domain models mirroring the storage schema, plus API-shaped composites."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CANON_PREFIX = "canon:"
USER_PREFIX = "usr_"

Tier = Literal["canonical", "overlay", "merged"]
MatchMethod = Literal["exact", "alias", "fuzzy", "created"]
ResolutionStatus = Literal["matched", "proposed", "created", "not_found"]
MemoryKind = Literal["fact", "preference", "event", "note"]
SourceType = Literal["assistant", "seed", "user", "inference"]


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def tier_of_id(entity_id: str) -> Tier:
    """Route an entity id to its home tier based on its prefix."""
    return "canonical" if entity_id.startswith(CANON_PREFIX) else "overlay"


class Provenance(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prov_"))
    source_type: SourceType = "assistant"
    assistant_id: str | None = None
    session_id: str | None = None
    stated_confidence: float | None = None
    derivation: dict[str, Any] | None = None
    raw_context: str | None = None
    recorded_at: str = Field(default_factory=utcnow)


class Entity(BaseModel):
    id: str
    entity_type: str
    name: str
    normalized_name: str
    summary: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    extends_id: str | None = None
    valid_from: str = Field(default_factory=utcnow)
    valid_to: str | None = None
    superseded_by: str | None = None
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class Alias(BaseModel):
    id: str = Field(default_factory=lambda: new_id("alias_"))
    entity_id: str
    alias: str
    normalized_alias: str
    alias_type: Literal["name", "abbreviation", "nickname", "description"] = "name"
    confidence: float = 1.0
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)


class Relationship(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rel_"))
    source_entity_id: str
    target_entity_id: str
    rel_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    valid_from: str = Field(default_factory=utcnow)
    valid_to: str | None = None
    superseded_by: str | None = None
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem_"))
    content: str
    memory_kind: MemoryKind = "fact"
    confidence: float = 1.0
    valid_from: str = Field(default_factory=utcnow)
    valid_to: str | None = None
    superseded_by: str | None = None
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)
    entity_ids: list[str] = Field(default_factory=list)


class Assertion(BaseModel):
    id: str = Field(default_factory=lambda: new_id("assert_"))
    seq: int | None = None
    kind: str
    subject_type: Literal["entity", "relationship", "memory", "alias", "proposal"]
    subject_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)


class MergeProposal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prop_"))
    candidate_name: str
    entity_id: str
    score: float
    status: Literal["pending", "accepted", "rejected"] = "pending"
    provenance_id: str
    created_at: str = Field(default_factory=utcnow)
    resolved_at: str | None = None


# ---------------------------------------------------------------------------
# API-shaped composites (returned by the fabric / MCP tools)
# ---------------------------------------------------------------------------


class ResolvedEntity(BaseModel):
    entity: Entity
    tier: Tier
    method: MatchMethod
    score: float = 100.0
    aliases: list[str] = Field(default_factory=list)


class ResolutionResult(BaseModel):
    status: ResolutionStatus
    entity: Entity | None = None
    tier: Tier | None = None
    method: MatchMethod | None = None
    score: float | None = None
    proposal_id: str | None = None
    near_misses: list[dict[str, Any]] = Field(default_factory=list)


class GraphEdge(BaseModel):
    relationship: Relationship
    source_name: str
    target_name: str
    tier: Tier
    provenance_summary: dict[str, Any] = Field(default_factory=dict)


class TimelineEvent(BaseModel):
    seq: int | None = None
    at: str
    kind: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
