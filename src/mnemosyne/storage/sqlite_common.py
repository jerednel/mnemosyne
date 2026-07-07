"""Shared SQLite read logic and row<->model conversion used by both tiers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from mnemosyne.models import (
    Alias,
    Assertion,
    Entity,
    Memory,
    MergeProposal,
    Provenance,
    Relationship,
)


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def row_to_entity(row: sqlite3.Row) -> Entity:
    return Entity(
        id=row["id"],
        entity_type=row["entity_type"],
        name=row["name"],
        normalized_name=row["normalized_name"],
        summary=row["summary"],
        attributes=_loads(row["attributes"]),
        confidence=row["confidence"],
        extends_id=row["extends_id"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        superseded_by=row["superseded_by"],
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_alias(row: sqlite3.Row) -> Alias:
    return Alias(
        id=row["id"],
        entity_id=row["entity_id"],
        alias=row["alias"],
        normalized_alias=row["normalized_alias"],
        alias_type=row["alias_type"],
        confidence=row["confidence"],
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
    )


def row_to_relationship(row: sqlite3.Row) -> Relationship:
    return Relationship(
        id=row["id"],
        source_entity_id=row["source_entity_id"],
        target_entity_id=row["target_entity_id"],
        rel_type=row["rel_type"],
        attributes=_loads(row["attributes"]),
        confidence=row["confidence"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        superseded_by=row["superseded_by"],
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
    )


def row_to_provenance(row: sqlite3.Row) -> Provenance:
    return Provenance(
        id=row["id"],
        source_type=row["source_type"],
        assistant_id=row["assistant_id"],
        session_id=row["session_id"],
        stated_confidence=row["stated_confidence"],
        derivation=json.loads(row["derivation"]) if row["derivation"] else None,
        raw_context=row["raw_context"],
        recorded_at=row["recorded_at"],
    )


def row_to_assertion(row: sqlite3.Row) -> Assertion:
    return Assertion(
        id=row["id"],
        seq=row["seq"],
        kind=row["kind"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        payload=_loads(row["payload"]),
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
    )


def row_to_proposal(row: sqlite3.Row) -> MergeProposal:
    return MergeProposal(
        id=row["id"],
        candidate_name=row["candidate_name"],
        entity_id=row["entity_id"],
        score=row["score"],
        status=row["status"],
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def row_to_memory(row: sqlite3.Row, entity_ids: list[str]) -> Memory:
    return Memory(
        id=row["id"],
        content=row["content"],
        memory_kind=row["memory_kind"],
        confidence=row["confidence"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        superseded_by=row["superseded_by"],
        provenance_id=row["provenance_id"],
        created_at=row["created_at"],
        entity_ids=entity_ids,
    )


class SqliteReadStore:
    """Read methods shared by both tiers."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return row_to_entity(row) if row else None

    def find_by_normalized_name(self, normalized_name: str) -> list[Entity]:
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE normalized_name = ? AND superseded_by IS NULL",
            (normalized_name,),
        ).fetchall()
        return [row_to_entity(r) for r in rows]

    def find_aliases(self, normalized_alias: str) -> list[Alias]:
        rows = self.conn.execute(
            "SELECT * FROM aliases WHERE normalized_alias = ?", (normalized_alias,)
        ).fetchall()
        return [row_to_alias(r) for r in rows]

    def aliases_for(self, entity_id: str) -> list[Alias]:
        rows = self.conn.execute(
            "SELECT * FROM aliases WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        return [row_to_alias(r) for r in rows]

    def all_aliases(self) -> list[Alias]:
        rows = self.conn.execute("SELECT * FROM aliases").fetchall()
        return [row_to_alias(r) for r in rows]

    def all_entities(self, entity_type: str | None = None) -> list[Entity]:
        if entity_type:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND superseded_by IS NULL",
                (entity_type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE superseded_by IS NULL"
            ).fetchall()
        return [row_to_entity(r) for r in rows]

    def relationships_for(self, entity_id: str) -> list[Relationship]:
        rows = self.conn.execute(
            "SELECT * FROM relationships WHERE source_entity_id = ? OR target_entity_id = ?",
            (entity_id, entity_id),
        ).fetchall()
        return [row_to_relationship(r) for r in rows]

    def get_provenance(self, provenance_id: str) -> Provenance | None:
        row = self.conn.execute(
            "SELECT * FROM provenance WHERE id = ?", (provenance_id,)
        ).fetchone()
        return row_to_provenance(row) if row else None

    def assertions_for(self, subject_id: str) -> list[Assertion]:
        rows = self.conn.execute(
            "SELECT * FROM assertions WHERE subject_id = ? ORDER BY seq", (subject_id,)
        ).fetchall()
        return [row_to_assertion(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
