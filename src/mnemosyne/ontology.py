"""Ontology type registry: entity types with inheritance, relationship taxonomy
with source/target constraints and functional (single-valued) semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SEED_TYPES_PATH = Path(__file__).parent / "seed" / "ontology_types.json"


class OntologyError(ValueError):
    """Raised when a type or relationship violates the registered ontology."""


@dataclass(frozen=True)
class EntityTypeDef:
    name: str
    parent: str | None = None
    description: str = ""


@dataclass(frozen=True)
class RelationshipTypeDef:
    name: str
    description: str = ""
    source_types: tuple[str, ...] = ("*",)
    target_types: tuple[str, ...] = ("*",)
    inverse_name: str | None = None
    functional: bool = False


@dataclass
class OntologyRegistry:
    entity_types: dict[str, EntityTypeDef] = field(default_factory=dict)
    relationship_types: dict[str, RelationshipTypeDef] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> OntologyRegistry:
        raw = json.loads((path or SEED_TYPES_PATH).read_text())
        registry = cls()
        for et in raw["entity_types"]:
            registry.entity_types[et["name"]] = EntityTypeDef(
                name=et["name"],
                parent=et.get("parent"),
                description=et.get("description", ""),
            )
        for rt in raw["relationship_types"]:
            registry.relationship_types[rt["name"]] = RelationshipTypeDef(
                name=rt["name"],
                description=rt.get("description", ""),
                source_types=tuple(rt.get("source_types", ["*"])),
                target_types=tuple(rt.get("target_types", ["*"])),
                inverse_name=rt.get("inverse_name"),
                functional=rt.get("functional", False),
            )
        return registry

    def validate_entity_type(self, entity_type: str) -> None:
        if entity_type not in self.entity_types:
            raise OntologyError(
                f"Unknown entity type {entity_type!r}. Valid types: {sorted(self.entity_types)}"
            )

    def is_subtype(self, candidate: str, ancestor: str) -> bool:
        """True if candidate == ancestor or candidate descends from it."""
        current: str | None = candidate
        seen: set[str] = set()
        while current is not None and current not in seen:
            if current == ancestor:
                return True
            seen.add(current)
            type_def = self.entity_types.get(current)
            current = type_def.parent if type_def else None
        return False

    def _matches_any(self, entity_type: str, allowed: tuple[str, ...]) -> bool:
        return "*" in allowed or any(self.is_subtype(entity_type, a) for a in allowed)

    def validate_relationship(self, rel_type: str, source_type: str, target_type: str) -> None:
        rel = self.relationship_types.get(rel_type)
        if rel is None:
            raise OntologyError(
                f"Unknown relationship type {rel_type!r}. "
                f"Valid types: {sorted(self.relationship_types)}"
            )
        if not self._matches_any(source_type, rel.source_types):
            raise OntologyError(
                f"{rel_type!r} requires source of type {list(rel.source_types)}, "
                f"got {source_type!r}"
            )
        if not self._matches_any(target_type, rel.target_types):
            raise OntologyError(
                f"{rel_type!r} requires target of type {list(rel.target_types)}, "
                f"got {target_type!r}"
            )

    def is_functional(self, rel_type: str) -> bool:
        rel = self.relationship_types.get(rel_type)
        return rel.functional if rel else False

    def describe(self) -> dict:
        """Serializable view for the list_ontology MCP tool."""
        return {
            "entity_types": [
                {"name": t.name, "parent": t.parent, "description": t.description}
                for t in self.entity_types.values()
            ],
            "relationship_types": [
                {
                    "name": r.name,
                    "description": r.description,
                    "source_types": list(r.source_types),
                    "target_types": list(r.target_types),
                    "inverse_name": r.inverse_name,
                    "functional": r.functional,
                }
                for r in self.relationship_types.values()
            ],
        }
