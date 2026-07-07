"""Read/write SQLite implementation of the private overlay tier.

Every write records provenance and appends an assertion row inside the same
transaction, so the append-only event log can never drift from state tables."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from mnemosyne.models import (
    Alias as AliasModel,
)
from mnemosyne.models import (
    Assertion,
    Entity,
    Memory,
    MergeProposal,
    Provenance,
    Relationship,
    utcnow,
)
from mnemosyne.storage.schema import init_schema
from mnemosyne.storage.sqlite_common import (
    SqliteReadStore,
    row_to_entity,
    row_to_memory,
    row_to_proposal,
)


class SqliteOverlayStore(SqliteReadStore):
    def __init__(self, db_path: Path | str, tier: str = "overlay", with_fts: bool = True):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        init_schema(conn, tier=tier, with_fts=with_fts)
        super().__init__(conn)
        self._fts_enabled = self._has_fts_table()

    def _has_fts_table(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memories_fts'"
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def save_provenance(self, provenance: Provenance) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO provenance
               (id, source_type, assistant_id, session_id, stated_confidence,
                derivation, raw_context, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provenance.id,
                provenance.source_type,
                provenance.assistant_id,
                provenance.session_id,
                provenance.stated_confidence,
                json.dumps(provenance.derivation) if provenance.derivation else None,
                provenance.raw_context,
                provenance.recorded_at,
            ),
        )

    def _append_assertion(
        self,
        kind: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any],
        provenance_id: str,
    ) -> Assertion:
        assertion = Assertion(
            kind=kind,
            subject_type=subject_type,  # type: ignore[arg-type]
            subject_id=subject_id,
            payload=payload,
            provenance_id=provenance_id,
        )
        cursor = self.conn.execute(
            """INSERT INTO assertions
               (id, kind, subject_type, subject_id, payload, provenance_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                assertion.id,
                assertion.kind,
                assertion.subject_type,
                assertion.subject_id,
                json.dumps(assertion.payload),
                assertion.provenance_id,
                assertion.created_at,
            ),
        )
        self.conn.execute(
            "UPDATE assertions SET seq = ? WHERE id = ?", (cursor.lastrowid, assertion.id)
        )
        assertion.seq = cursor.lastrowid
        return assertion

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def create_entity(self, entity: Entity, provenance: Provenance) -> Entity:
        with self.conn:
            self.save_provenance(provenance)
            self.conn.execute(
                """INSERT INTO entities
                   (id, entity_type, name, normalized_name, summary, attributes, confidence,
                    extends_id, valid_from, valid_to, superseded_by, provenance_id,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity.id,
                    entity.entity_type,
                    entity.name,
                    entity.normalized_name,
                    entity.summary,
                    json.dumps(entity.attributes),
                    entity.confidence,
                    entity.extends_id,
                    entity.valid_from,
                    entity.valid_to,
                    entity.superseded_by,
                    provenance.id,
                    entity.created_at,
                    entity.updated_at,
                ),
            )
            self._append_assertion(
                "entity_created",
                "entity",
                entity.id,
                entity.model_dump(exclude={"provenance_id"}),
                provenance.id,
            )
        return entity

    def add_alias(self, alias: AliasModel, provenance: Provenance) -> AliasModel:
        with self.conn:
            self.save_provenance(provenance)
            self.conn.execute(
                """INSERT OR IGNORE INTO aliases
                   (id, entity_id, alias, normalized_alias, alias_type, confidence,
                    provenance_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    alias.id,
                    alias.entity_id,
                    alias.alias,
                    alias.normalized_alias,
                    alias.alias_type,
                    alias.confidence,
                    provenance.id,
                    alias.created_at,
                ),
            )
            self._append_assertion(
                "alias_added",
                "alias",
                alias.id,
                {
                    "entity_id": alias.entity_id,
                    "alias": alias.alias,
                    "alias_type": alias.alias_type,
                    "confidence": alias.confidence,
                },
                provenance.id,
            )
        return alias

    def _insert_relationship(self, rel: Relationship, provenance_id: str) -> None:
        self.conn.execute(
            """INSERT INTO relationships
               (id, source_entity_id, target_entity_id, rel_type, attributes, confidence,
                valid_from, valid_to, superseded_by, provenance_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rel.id,
                rel.source_entity_id,
                rel.target_entity_id,
                rel.rel_type,
                json.dumps(rel.attributes),
                rel.confidence,
                rel.valid_from,
                rel.valid_to,
                rel.superseded_by,
                provenance_id,
                rel.created_at,
            ),
        )

    def assert_relationship(
        self, relationship: Relationship, provenance: Provenance
    ) -> Relationship:
        with self.conn:
            self.save_provenance(provenance)
            self._insert_relationship(relationship, provenance.id)
            self._append_assertion(
                "relationship_asserted",
                "relationship",
                relationship.id,
                relationship.model_dump(exclude={"provenance_id"}),
                provenance.id,
            )
        return relationship

    def supersede_relationship(
        self, old_id: str, new_relationship: Relationship, provenance: Provenance
    ) -> Relationship:
        """The temporal invariant: insert the new fact, close the old fact's
        validity window, and link supersession — one transaction, both logged."""
        with self.conn:
            self.save_provenance(provenance)
            self._insert_relationship(new_relationship, provenance.id)
            self.conn.execute(
                "UPDATE relationships SET valid_to = ?, superseded_by = ? WHERE id = ?",
                (new_relationship.valid_from, new_relationship.id, old_id),
            )
            self._append_assertion(
                "relationship_asserted",
                "relationship",
                new_relationship.id,
                new_relationship.model_dump(exclude={"provenance_id"}),
                provenance.id,
            )
            self._append_assertion(
                "relationship_superseded",
                "relationship",
                old_id,
                {"superseded_by": new_relationship.id, "valid_to": new_relationship.valid_from},
                provenance.id,
            )
        return new_relationship

    def close_relationship(
        self, old_id: str, superseded_by: str, valid_to: str, provenance: Provenance
    ) -> None:
        """Close an already-open fact's validity window (used when one new fact
        supersedes several old ones); the supersession is logged like any write."""
        with self.conn:
            self.save_provenance(provenance)
            self.conn.execute(
                "UPDATE relationships SET valid_to = ?, superseded_by = ? WHERE id = ?",
                (valid_to, superseded_by, old_id),
            )
            self._append_assertion(
                "relationship_superseded",
                "relationship",
                old_id,
                {"superseded_by": superseded_by, "valid_to": valid_to},
                provenance.id,
            )

    def record_memory(self, memory: Memory, provenance: Provenance) -> Memory:
        with self.conn:
            self.save_provenance(provenance)
            self.conn.execute(
                """INSERT INTO memories
                   (id, content, memory_kind, confidence, valid_from, valid_to,
                    superseded_by, provenance_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory.id,
                    memory.content,
                    memory.memory_kind,
                    memory.confidence,
                    memory.valid_from,
                    memory.valid_to,
                    memory.superseded_by,
                    provenance.id,
                    memory.created_at,
                ),
            )
            for entity_id in memory.entity_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?, ?)",
                    (memory.id, entity_id),
                )
            if self._fts_enabled:
                self.conn.execute(
                    """INSERT INTO memories_fts (rowid, content)
                       SELECT rowid, content FROM memories WHERE id = ?""",
                    (memory.id,),
                )
            self._append_assertion(
                "memory_recorded",
                "memory",
                memory.id,
                memory.model_dump(exclude={"provenance_id"}),
                provenance.id,
            )
        return memory

    def create_proposal(self, proposal: MergeProposal, provenance: Provenance) -> MergeProposal:
        with self.conn:
            self.save_provenance(provenance)
            self.conn.execute(
                """INSERT INTO merge_proposals
                   (id, candidate_name, entity_id, score, status, provenance_id,
                    created_at, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.id,
                    proposal.candidate_name,
                    proposal.entity_id,
                    proposal.score,
                    proposal.status,
                    provenance.id,
                    proposal.created_at,
                    proposal.resolved_at,
                ),
            )
            self._append_assertion(
                "merge_proposed",
                "proposal",
                proposal.id,
                proposal.model_dump(exclude={"provenance_id"}),
                provenance.id,
            )
        return proposal

    def resolve_proposal(
        self, proposal_id: str, status: str, provenance: Provenance
    ) -> MergeProposal:
        if status not in ("accepted", "rejected"):
            raise ValueError(f"Invalid proposal resolution status: {status!r}")
        with self.conn:
            self.save_provenance(provenance)
            resolved_at = utcnow()
            self.conn.execute(
                "UPDATE merge_proposals SET status = ?, resolved_at = ? WHERE id = ?",
                (status, resolved_at, proposal_id),
            )
            self._append_assertion(
                f"merge_{status}",
                "proposal",
                proposal_id,
                {"status": status, "resolved_at": resolved_at},
                provenance.id,
            )
        proposal = self.get_proposal(proposal_id)
        assert proposal is not None
        return proposal

    # ------------------------------------------------------------------
    # reads specific to the overlay
    # ------------------------------------------------------------------

    def entity_extending(self, extends_id: str) -> Entity | None:
        """The overlay entity (if any) that enriches/shadows a canonical entity."""
        row = self.conn.execute(
            "SELECT * FROM entities WHERE extends_id = ? AND superseded_by IS NULL",
            (extends_id,),
        ).fetchone()
        return row_to_entity(row) if row else None

    def get_memory(self, memory_id: str) -> Memory | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return None
        return row_to_memory(row, self._entity_ids_for_memory(memory_id))

    def _entity_ids_for_memory(self, memory_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT entity_id FROM memory_entities WHERE memory_id = ?", (memory_id,)
        ).fetchall()
        return [r["entity_id"] for r in rows]

    def search_memories(self, query: str, limit: int = 10) -> list[Memory]:
        tokens = re.findall(r"\w+", query)
        if not tokens:
            return []
        if self._fts_enabled:
            fts_query = " ".join(f'"{t}"' for t in tokens)
            rows = self.conn.execute(
                """SELECT m.* FROM memories m
                   JOIN memories_fts f ON f.rowid = m.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        else:
            clause = " AND ".join("content LIKE ?" for _ in tokens)
            rows = self.conn.execute(
                f"SELECT * FROM memories WHERE {clause} LIMIT ?",
                tuple(f"%{t}%" for t in tokens) + (limit,),
            ).fetchall()
        return [row_to_memory(r, self._entity_ids_for_memory(r["id"])) for r in rows]

    def memories_for_entity(self, entity_id: str, limit: int = 50) -> list[Memory]:
        rows = self.conn.execute(
            """SELECT m.* FROM memories m
               JOIN memory_entities me ON me.memory_id = m.id
               WHERE me.entity_id = ?
               ORDER BY m.created_at DESC LIMIT ?""",
            (entity_id, limit),
        ).fetchall()
        return [row_to_memory(r, self._entity_ids_for_memory(r["id"])) for r in rows]

    def list_proposals(self, status: str | None = "pending") -> list[MergeProposal]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM merge_proposals WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM merge_proposals ORDER BY created_at").fetchall()
        return [row_to_proposal(r) for r in rows]

    def get_proposal(self, proposal_id: str) -> MergeProposal | None:
        row = self.conn.execute(
            "SELECT * FROM merge_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return row_to_proposal(row) if row else None
