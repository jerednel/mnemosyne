"""Temporal predicates and timeline assembly over the append-only assertion log.

Invariant: facts are never updated in place. A fact is superseded by inserting
its replacement, closing the old validity window, and linking superseded_by."""

from __future__ import annotations

from mnemosyne.models import Assertion, Provenance, TimelineEvent


def is_current(fact, now: str | None = None) -> bool:
    """A fact is current if it has not been superseded and its validity window
    is still open (or extends past `now`)."""
    if fact.superseded_by is not None:
        return False
    if fact.valid_to is None:
        return True
    return now is not None and fact.valid_to > now


def is_valid_at(fact, at: str) -> bool:
    """True if the fact's validity window covers instant `at` (ISO-8601)."""
    if fact.valid_from > at:
        return False
    return fact.valid_to is None or fact.valid_to > at


_KIND_SUMMARIES = {
    "entity_created": "Entity created",
    "entity_updated": "Entity updated",
    "alias_added": "Alias added",
    "relationship_asserted": "Relationship asserted",
    "relationship_superseded": "Relationship superseded",
    "memory_recorded": "Memory recorded",
    "memory_superseded": "Memory superseded",
    "merge_proposed": "Merge proposed",
    "merge_accepted": "Merge accepted",
    "merge_rejected": "Merge rejected",
}


def summarize_assertion(assertion: Assertion) -> str:
    base = _KIND_SUMMARIES.get(assertion.kind, assertion.kind)
    payload = assertion.payload
    if assertion.kind == "entity_created" and "name" in payload:
        return f"{base}: {payload['name']} ({payload.get('entity_type', '?')})"
    if assertion.kind == "relationship_asserted" and "rel_type" in payload:
        return (
            f"{base}: {payload.get('source_entity_id', '?')} "
            f"-[{payload['rel_type']}]-> {payload.get('target_entity_id', '?')}"
        )
    if assertion.kind == "relationship_superseded":
        return f"{base}: replaced by {payload.get('superseded_by', '?')}"
    if assertion.kind == "memory_recorded" and "content" in payload:
        content = payload["content"]
        return f"{base}: {content[:80]}{'…' if len(content) > 80 else ''}"
    if assertion.kind == "alias_added" and "alias" in payload:
        return f"{base}: {payload['alias']!r} -> {payload.get('entity_id', '?')}"
    return base


def build_timeline(
    assertions: list[Assertion],
    provenance_lookup: dict[str, Provenance] | None = None,
) -> list[TimelineEvent]:
    """Convert raw assertion rows into an ordered, human-readable history."""
    provenance_lookup = provenance_lookup or {}
    events = []
    for a in sorted(assertions, key=lambda x: (x.created_at, x.seq or 0)):
        prov = provenance_lookup.get(a.provenance_id)
        events.append(
            TimelineEvent(
                seq=a.seq,
                at=a.created_at,
                kind=a.kind,
                summary=summarize_assertion(a),
                payload=a.payload,
                provenance={
                    "source_type": prov.source_type if prov else None,
                    "assistant_id": prov.assistant_id if prov else None,
                    "stated_confidence": prov.stated_confidence if prov else None,
                }
                if prov
                else {},
            )
        )
    return events
