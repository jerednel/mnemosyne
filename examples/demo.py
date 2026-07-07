"""Mnemosyne in 30 seconds.

Runs the full memory-fabric story against a throwaway data directory:
identity resolution, private overlays on shared entities, temporal
supersession, time-travel queries, and provenance.

    uv run python examples/demo.py
"""

import tempfile
from pathlib import Path

from mnemosyne.fabric import MemoryFabric
from mnemosyne.models import Provenance
from mnemosyne.seed.loader import build_canonical_db
from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore
from mnemosyne.storage.sqlite_overlay import SqliteOverlayStore


def section(title: str) -> None:
    print(f"\n{'─' * 64}\n{title}\n{'─' * 64}")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        build_canonical_db(data / "canonical.db")
        fabric = MemoryFabric(
            canonical=SqliteCanonicalStore(data / "canonical.db"),
            overlay=SqliteOverlayStore(data / "overlay.db"),
        )
        prov = Provenance(assistant_id="demo-assistant/1.0", stated_confidence=0.9)

        section("1. Identity resolution — mentions become canonical entities")
        for mention in ["DBX", "the lakehouse vendor", "Databrics", "Postgres"]:
            r = fabric.resolve_entity(mention, prov)
            print(f"  {mention!r:26} -> {r.entity.name:12} ({r.method}, score {r.score:.0f})")

        section("2. Private overlay — enrich a shared entity without touching it")
        fabric.create_entity(
            "Databricks",
            "company",
            prov,
            aliases=["that client"],
            summary="Our data platform vendor; renewal in Q3.",
            attributes={"account_owner": "Jeremy"},
            extends="canon:company/databricks",
        )
        merged, tier = fabric.get_entity("canon:company/databricks")
        print(f"  merged view [{tier}]: {merged.summary}")
        print(f"  attributes: {merged.attributes}")
        r = fabric.resolve_entity("that client", prov)
        print(f"  'that client' now resolves -> {r.entity.name}")

        section("3. Temporal memory — facts supersede, they don't overwrite")
        fabric.assert_relationship(
            "Jeremy Nelson",
            "Databricks",
            "works_at",
            prov,
            valid_from="2024-01-01T00:00:00+00:00",
        )
        result = fabric.assert_relationship(
            "Jeremy Nelson",
            "Anthropic",
            "works_at",
            prov,
            valid_from="2025-06-01T00:00:00+00:00",
        )
        print(f"  new works_at edge auto-superseded {len(result['superseded'])} prior fact(s)")
        jeremy = fabric.resolve_entity("Jeremy Nelson", prov).entity
        for label, as_of in [("as of 2024-07", "2024-07-01T00:00:00+00:00"), ("today", None)]:
            edges = fabric.query_graph(
                jeremy.id,
                rel_type="works_at",
                as_of=as_of,
                include_superseded=as_of is not None,
            )
            employers = ", ".join(e.target_name for e in edges)
            print(f"  employer {label}: {employers}")

        section("4. Structured recall — memories linked to resolved entities")
        fabric.remember(
            "Jeremy is evaluating DBX for the lakehouse migration",
            prov,
            mentions=["Jeremy Nelson", "DBX"],
        )
        hit = fabric.recall("lakehouse migration", prov)["memories"][0]
        print(f"  recall('lakehouse migration') -> {hit['memory'].content!r}")
        print(f"  linked entities: {hit['entities']}")
        print(f"  provenance: {hit['provenance']}")

        section("5. Provenance — every fact knows where it came from")
        for event in fabric.entity_timeline(jeremy.id):
            src = event.provenance.get("assistant_id") or event.provenance.get("source_type")
            print(f"  [{src}] {event.summary}")

        print("\nDone. Same fabric, any MCP client: uv run mnemosyne-server\n")


if __name__ == "__main__":
    main()
