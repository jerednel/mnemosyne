"""Offline canonical-tier builder — the ONLY write path to canonical.db.

Reuses the overlay store's transactional writers (so seed rows get full
assertion history and provenance) but stamps tier='canonical' and
source_type='seed' provenance. At runtime the resulting file is opened
strictly read-only.

Loads the curated base seed (canonical_entities.json) first, then any
extended seed artifacts in seed/data/ (*.json / *.json.gz — e.g. the
Wikidata import). Base entities are authoritative: an extended entity whose
normalized name already exists is skipped and its aliases are merged onto
the existing entity instead."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path
from typing import Any

from mnemosyne.config import canonical_db_path
from mnemosyne.models import Alias, Entity, Provenance, Relationship, utcnow
from mnemosyne.ontology import OntologyError, OntologyRegistry
from mnemosyne.resolution import normalize
from mnemosyne.storage.sqlite_overlay import SqliteOverlayStore

SEED_ENTITIES_PATH = Path(__file__).parent / "canonical_entities.json"
SEED_DATA_DIR = Path(__file__).parent / "data"


def _read_payload(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _load_payload(
    store: SqliteOverlayStore,
    ontology: OntologyRegistry,
    payload: dict[str, Any],
    provenance: Provenance,
    now: str,
    entity_types: dict[str, str],
    names_seen: dict[str, str],
    dedupe: bool,
) -> dict[str, int]:
    """Load one seed payload. Returns counts. `names_seen` maps normalized
    name -> entity id across all payloads; with dedupe=True, colliding
    entities are skipped and their aliases merged onto the existing entity."""
    stats = {
        "entities": 0,
        "aliases": 0,
        "relationships": 0,
        "skipped_duplicates": 0,
        "skipped_invalid_rels": 0,
    }
    id_remap: dict[str, str] = {}

    for raw_entity in payload.get("entities", []):
        ontology.validate_entity_type(raw_entity["entity_type"])
        normalized = normalize(raw_entity["name"])
        existing_id = names_seen.get(normalized)
        alias_rows = raw_entity.get("aliases", [])

        if dedupe and existing_id is not None:
            # Base seed wins: keep the existing entity, adopt the newcomer's
            # aliases so its spellings still resolve.
            id_remap[raw_entity["id"]] = existing_id
            stats["skipped_duplicates"] += 1
            target_id = existing_id
        else:
            entity = Entity(
                id=raw_entity["id"],
                entity_type=raw_entity["entity_type"],
                name=raw_entity["name"],
                normalized_name=normalized,
                summary=raw_entity.get("summary"),
                attributes=raw_entity.get("attributes", {}),
                valid_from=now,
                provenance_id=provenance.id,
            )
            store.create_entity(entity, provenance)
            entity_types[entity.id] = entity.entity_type
            names_seen.setdefault(normalized, entity.id)
            stats["entities"] += 1
            target_id = entity.id

        for raw_alias in alias_rows:
            normalized_alias = normalize(raw_alias["alias"])
            if not normalized_alias:
                continue
            store.add_alias(
                Alias(
                    entity_id=target_id,
                    alias=raw_alias["alias"],
                    normalized_alias=normalized_alias,
                    alias_type=raw_alias.get("alias_type", "name"),
                    provenance_id=provenance.id,
                ),
                provenance,
            )
            stats["aliases"] += 1

    for raw_rel in payload.get("relationships", []):
        source = id_remap.get(raw_rel["source"], raw_rel["source"])
        target = id_remap.get(raw_rel["target"], raw_rel["target"])
        source_type = entity_types.get(source)
        target_type = entity_types.get(target)
        if source_type is None or target_type is None or source == target:
            stats["skipped_invalid_rels"] += 1
            continue
        try:
            ontology.validate_relationship(raw_rel["rel_type"], source_type, target_type)
        except OntologyError:
            stats["skipped_invalid_rels"] += 1
            continue
        store.assert_relationship(
            Relationship(
                source_entity_id=source,
                target_entity_id=target,
                rel_type=raw_rel["rel_type"],
                attributes=raw_rel.get("attributes", {}),
                valid_from=now,
                provenance_id=provenance.id,
            ),
            provenance,
        )
        stats["relationships"] += 1
    return stats


_AUTO = object()


def build_canonical_db(
    db_path: Path,
    seed_path: Path = SEED_ENTITIES_PATH,
    force: bool = False,
    extended_dir: Path | None | object = _AUTO,
) -> int:
    """Build canonical.db from base + extended seed data. Returns total
    entities loaded. By default extended artifacts in seed/data/ are included;
    set MNEMOSYNE_SEED_BASE_ONLY=1 (or pass extended_dir=None) to skip them."""
    if extended_dir is _AUTO:
        extended_dir = None if os.environ.get("MNEMOSYNE_SEED_BASE_ONLY") else SEED_DATA_DIR
    if db_path.exists():
        if not force:
            raise FileExistsError(f"{db_path} already exists. Pass --force to rebuild it.")
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            sidecar.unlink(missing_ok=True)

    ontology = OntologyRegistry.load()
    store = SqliteOverlayStore(db_path, tier="canonical", with_fts=False)
    # The build is a deterministic offline artifact — trade crash safety for
    # speed so tens of thousands of per-row transactions don't fsync-crawl.
    store.conn.execute("PRAGMA synchronous = OFF")
    now = utcnow()
    entity_types: dict[str, str] = {}
    names_seen: dict[str, str] = {}
    total_entities = 0

    base_provenance = Provenance(
        source_type="seed",
        assistant_id="mnemosyne-seed-loader",
        derivation={"seed_file": seed_path.name},
    )
    base_stats = _load_payload(
        store,
        ontology,
        _read_payload(seed_path),
        base_provenance,
        now,
        entity_types,
        names_seen,
        dedupe=False,
    )
    total_entities += base_stats["entities"]
    store.conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        (f"import:{seed_path.name}", json.dumps(base_stats)),
    )

    if extended_dir is not None and extended_dir.is_dir():
        for data_file in sorted(extended_dir.glob("*.json*")):
            payload = _read_payload(data_file)
            provenance = Provenance(
                source_type="seed",
                assistant_id="mnemosyne-seed-loader",
                derivation={
                    "seed_file": data_file.name,
                    "source": payload.get("source"),
                    "license": payload.get("license"),
                },
            )
            stats = _load_payload(
                store,
                ontology,
                payload,
                provenance,
                now,
                entity_types,
                names_seen,
                dedupe=True,
            )
            total_entities += stats["entities"]
            store.conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                (
                    f"import:{data_file.name}",
                    json.dumps({**stats, "source": payload.get("source")}),
                ),
            )

    store.conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('seeded_at', ?)", (now,)
    )
    store.conn.commit()
    # Fold WAL sidecar files back into the main db so the file is standalone
    # and read-only opens cleanly.
    store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.conn.execute("PRAGMA journal_mode = DELETE")
    store.close()
    return total_entities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Mnemosyne canonical ontology DB.")
    parser.add_argument(
        "--db", type=Path, default=None, help="Output path (default: <data-dir>/canonical.db)"
    )
    parser.add_argument("--force", action="store_true", help="Rebuild even if the DB exists.")
    parser.add_argument(
        "--no-extended",
        action="store_true",
        help="Load only the base seed, skipping seed/data/ artifacts.",
    )
    args = parser.parse_args(argv)
    db_path = args.db or canonical_db_path()
    try:
        count = build_canonical_db(
            db_path,
            force=args.force,
            extended_dir=None if args.no_extended else SEED_DATA_DIR,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Seeded {count} canonical entities into {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
