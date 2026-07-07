"""Importer transform, extended-seed loading with dedupe, and the coverage
report — all offline (no network; SPARQL rows are synthetic fixtures)."""

import gzip
import json

from mnemosyne.seed.coverage import generate_coverage_markdown
from mnemosyne.seed.loader import build_canonical_db
from mnemosyne.seed.wikidata import ALIAS_SEP, SLICES, build_seed_payload
from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore


def _row(qid, label, description=None, aliases=(), inception=None, website=None):
    row = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "label": {"value": label},
        "sitelinks": {"value": "50"},
    }
    if description:
        row["description"] = {"value": description}
    if aliases:
        row["aliases"] = {"value": ALIAS_SEP.join(aliases)}
    if inception:
        row["inception"] = {"value": inception}
    if website:
        row["website"] = {"value": website}
    return row


def test_build_seed_payload_transform():
    slice_rows = {
        "software_companies": [
            _row(
                "Q100",
                "Acme Software",
                "makes software",
                aliases=["ACME", "Acme Inc"],
                inception="1999-04-01T00:00:00Z",
                website="https://acme.example",
            ),
            _row("Q101", "Q101"),  # unlabeled -> dropped
            _row("Q102", "Duplicate Name"),
        ],
        "technology_companies": [
            _row("Q100", "Acme Software"),  # duplicate QID -> first slice wins
            _row("Q103", "Duplicate Name"),  # slug collision -> QID suffix
        ],
        "programming_languages": [_row("Q200", "AcmeLang", aliases=["AL"])],
        "software": [],
        "free_software": [],
        "websites": [],
    }
    relation_pairs = {
        "P178": [("Q200", "Q100"), ("Q200", "Q999")],  # Q999 outside import -> dropped
        "P127": [],
        "P361": [],
    }
    result = build_seed_payload(slice_rows, relation_pairs, SLICES)
    payload = result.payload

    ids = {e["id"]: e for e in payload["entities"]}
    assert "canon:company/acme-software" in ids
    acme = ids["canon:company/acme-software"]
    assert acme["attributes"]["wikidata_qid"] == "Q100"
    assert acme["attributes"]["founded"] == 1999
    assert {a["alias"] for a in acme["aliases"]} == {"ACME", "Acme Inc"}
    # Unlabeled row dropped; duplicate QID not re-imported.
    assert result.stats["entities"] == 4
    # Slug collision suffixed with the QID.
    assert "canon:company/duplicate-name" in ids
    assert "canon:company/duplicate-name-q103" in ids
    # P178 developer is reversed into develops(company -> language); the pair
    # referencing an un-imported QID is dropped.
    assert payload["relationships"] == [
        {
            "source": "canon:company/acme-software",
            "target": "canon:technology/acmelang",
            "rel_type": "develops",
        }
    ]


def test_loader_merges_extended_seed_with_dedupe(tmp_path):
    extended = {
        "source": "wikidata",
        "license": "CC0-1.0",
        "entities": [
            {
                # Collides with the base seed's Databricks by normalized name:
                # entity is skipped, alias merged onto the existing entity.
                "id": "canon:company/databricks-q18334571",
                "entity_type": "company",
                "name": "Databricks",
                "summary": "wikidata copy",
                "attributes": {"wikidata_qid": "Q18334571"},
                "aliases": [{"alias": "Databricks Inc branding", "alias_type": "name"}],
            },
            {
                "id": "canon:company/acme-software",
                "entity_type": "company",
                "name": "Acme Software",
                "summary": "New from Wikidata",
                "attributes": {"wikidata_qid": "Q100"},
                "aliases": [{"alias": "ACME", "alias_type": "name"}],
            },
        ],
        "relationships": [
            # Valid: imported company develops base-seed technology.
            {
                "source": "canon:company/acme-software",
                "target": "canon:technology/apache-spark",
                "rel_type": "develops",
            },
            # Invalid per ontology (develops requires organization source).
            {
                "source": "canon:technology/apache-spark",
                "target": "canon:company/acme-software",
                "rel_type": "develops",
            },
        ],
    }
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "wikidata_test.json.gz").write_bytes(gzip.compress(json.dumps(extended).encode()))

    db_path = tmp_path / "canonical.db"
    build_canonical_db(db_path, extended_dir=data_dir)
    store = SqliteCanonicalStore(db_path)
    try:
        # Deduped: exactly one Databricks, and it's the curated base one.
        databricks = store.find_by_normalized_name("databricks")
        assert [e.id for e in databricks] == ["canon:company/databricks"]
        # ...but the newcomer's alias resolves to it.
        merged_alias = store.find_aliases("databricks inc branding")
        assert merged_alias[0].entity_id == "canon:company/databricks"
        # New entity landed with QID provenance in attributes.
        acme = store.get_entity("canon:company/acme-software")
        assert acme.attributes["wikidata_qid"] == "Q100"
        # Valid relationship loaded; invalid one skipped.
        rels = store.relationships_for("canon:company/acme-software")
        assert [(r.rel_type, r.target_entity_id) for r in rels] == [
            ("develops", "canon:technology/apache-spark")
        ]
        # Import stats recorded for the coverage report.
        row = store.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'import:wikidata_test.json.gz'"
        ).fetchone()
        stats = json.loads(row[0])
        assert stats["skipped_duplicates"] == 1
        assert stats["skipped_invalid_rels"] == 1
    finally:
        store.close()


def test_base_only_env_skips_extended(tmp_path, monkeypatch):
    import mnemosyne.seed.loader as loader_module

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "extra.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "canon:company/env-switch-probe",
                        "entity_type": "company",
                        "name": "Env Switch Probe",
                        "aliases": [],
                    }
                ],
                "relationships": [],
            }
        )
    )
    monkeypatch.setattr(loader_module, "SEED_DATA_DIR", data_dir)

    # Default: extended data loads.
    with_ext = tmp_path / "with.db"
    build_canonical_db(with_ext)
    store = SqliteCanonicalStore(with_ext)
    assert len(store.find_by_normalized_name("env switch probe")) == 1
    store.close()

    # MNEMOSYNE_SEED_BASE_ONLY=1: extended data skipped.
    monkeypatch.setenv("MNEMOSYNE_SEED_BASE_ONLY", "1")
    base_only = tmp_path / "base.db"
    build_canonical_db(base_only)
    store = SqliteCanonicalStore(base_only)
    assert store.find_by_normalized_name("env switch probe") == []
    store.close()


def test_coverage_report(tmp_path):
    db_path = tmp_path / "canonical.db"
    build_canonical_db(db_path, extended_dir=None)
    markdown = generate_coverage_markdown(db_path)
    assert "# Ontology Coverage" in markdown
    assert "| company |" in markdown
    assert "curated base seed" in markdown
    assert "Databricks" in markdown  # most-aliased table
    assert "comprehensive by domain, not universal" in markdown
