"""Wikidata importer: builds canonical-tier seed data from scoped SPARQL slices.

Comprehensive-by-domain, not universal: each slice targets one entity class
(software companies, programming languages, ...) with a notability floor
(sitelink count). Output is a seed JSON artifact (same shape as
canonical_entities.json, gzipped) that the loader ingests offline — the
canonical DB stays a deterministic build with no network at startup.

Every imported row records its Wikidata QID, so provenance traces each
canonical fact to its public source.

    uv run mnemosyne-import --out src/mnemosyne/seed/data/wikidata_core.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from mnemosyne.resolution import normalize

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "MnemosyneImporter/0.1 (https://github.com/jerednel/jerednel; jnelson563@outlook.com)"
DEFAULT_OUT = Path(__file__).parent / "data" / "wikidata_core.json.gz"

# Unit-separator control char: safe GROUP_CONCAT delimiter for alias strings.
ALIAS_SEP = "\x1f"


@dataclass(frozen=True)
class Slice:
    name: str
    wikidata_class: str  # QID matched via direct P31
    entity_type: str  # Mnemosyne ontology type
    min_sitelinks: int
    description: str


# Ordered: earlier slices win when a QID appears in more than one. Membership
# uses p:P31/ps:P31 (all non-deprecated ranks): wdt: alone hides statements
# outranked by a more specific type (e.g. Rust's 'programming language').
SLICES: list[Slice] = [
    Slice("software_companies", "Q1058914", "company", 4, "Software companies"),
    Slice("technology_companies", "Q18388277", "company", 5, "Technology companies"),
    Slice("game_developers", "Q210167", "company", 8, "Video game developers/studios"),
    Slice("programming_languages", "Q9143", "technology", 3, "Programming languages"),
    Slice("software", "Q7397", "technology", 12, "Notable software"),
    Slice("free_software", "Q341", "technology", 8, "Free software"),
    Slice("foss", "Q506883", "technology", 8, "Free and open-source software"),
    Slice("software_libraries", "Q188860", "technology", 4, "Software libraries"),
    Slice("software_frameworks", "Q271680", "technology", 4, "Software frameworks"),
    Slice("operating_systems", "Q9135", "technology", 5, "Operating systems"),
    Slice("dbms", "Q176165", "technology", 3, "Database management systems"),
    Slice("rdbms", "Q1130645", "technology", 3, "Relational database management systems"),
    Slice("public_domain_software", "Q1037852", "technology", 5, "Public-domain software"),
    Slice("web_services", "Q193424", "product", 5, "Web services"),
    Slice("internet_services", "Q1668024", "product", 8, "Internet services/platforms"),
    Slice("websites", "Q35127", "product", 15, "Major websites/platforms"),
]

# Wikidata property -> (Mnemosyne rel_type, direction). "forward" keeps
# item->value; "reverse" flips it (P178 'developer' means value develops item).
RELATION_PROPS: dict[str, tuple[str, str]] = {
    "P178": ("develops", "reverse"),
    "P127": ("owns", "reverse"),
    "P361": ("part_of", "forward"),
}

# Labels/aliases accept both "en" and "mul": Wikidata migrates labels that are
# identical across languages to the multilingual "mul" code, so en-only
# filtering silently drops entities like Rust, Docker, and SQLite.
ENTITY_QUERY = """
SELECT ?item ?label ?sitelinks
       (SAMPLE(?descriptionRaw) AS ?description)
       (SAMPLE(?inceptionRaw) AS ?inception) (SAMPLE(?websiteRaw) AS ?website)
       (GROUP_CONCAT(DISTINCT ?alt; separator="\\u001F") AS ?aliases)
WHERE {{
  ?item p:P31/ps:P31 wd:{wikidata_class} .
  ?item wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks >= {min_sitelinks})
  ?item rdfs:label ?label FILTER(LANG(?label) IN ("en", "mul")) .
  OPTIONAL {{
    ?item schema:description ?descriptionRaw
    FILTER(LANG(?descriptionRaw) IN ("en", "mul"))
  }}
  OPTIONAL {{ ?item skos:altLabel ?alt FILTER(LANG(?alt) IN ("en", "mul")) }}
  OPTIONAL {{ ?item wdt:P571 ?inceptionRaw }}
  OPTIONAL {{ ?item wdt:P856 ?websiteRaw }}
}}
GROUP BY ?item ?label ?sitelinks
ORDER BY DESC(?sitelinks)
LIMIT {limit}
"""

RELATION_QUERY = """
SELECT ?item ?value WHERE {{
  VALUES ?item {{ {qids} }}
  ?item wdt:{prop} ?value .
}}
"""


def sparql(client: httpx.Client, query: str, retries: int = 3) -> list[dict[str, Any]]:
    delay = 2.0
    for attempt in range(retries + 1):
        response = client.get(
            SPARQL_ENDPOINT,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
        )
        # WDQS throws transient 5xx/429 under load — retry with backoff.
        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response.json()["results"]["bindings"]
    raise RuntimeError("unreachable")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(label)).strip("-")
    return slug or "unnamed"


@dataclass
class ImportResult:
    payload: dict[str, Any]
    stats: dict[str, Any] = field(default_factory=dict)


def build_seed_payload(
    slice_rows: dict[str, list[dict[str, Any]]],
    relation_pairs: dict[str, list[tuple[str, str]]],
    slices: list[Slice],
) -> ImportResult:
    """Pure transform: SPARQL rows -> loader-shaped seed payload. Earlier
    slices win duplicate QIDs; duplicate slugs get a QID suffix."""
    slice_by_name = {s.name: s for s in slices}
    entities: list[dict[str, Any]] = []
    id_by_qid: dict[str, str] = {}
    used_ids: set[str] = set()
    per_slice_counts: dict[str, int] = {}

    for slice_name, rows in slice_rows.items():
        spec = slice_by_name[slice_name]
        count = 0
        for row in rows:
            qid = _qid(row["item"]["value"])
            if qid in id_by_qid:
                continue
            label = row.get("label", {}).get("value", "").strip()
            if not label or (label.startswith("Q") and label[1:].isdigit()):
                continue  # unlabeled-in-English rows are useless for resolution
            entity_id = f"canon:{spec.entity_type}/{_slug(label)}"
            if entity_id in used_ids:
                entity_id = f"{entity_id}-{qid.lower()}"
            used_ids.add(entity_id)
            id_by_qid[qid] = entity_id

            aliases = []
            raw_aliases = row.get("aliases", {}).get("value", "")
            for alt in raw_aliases.split(ALIAS_SEP):
                alt = alt.strip()
                if alt and normalize(alt) and normalize(alt) != normalize(label):
                    aliases.append({"alias": alt, "alias_type": "name"})

            attributes: dict[str, Any] = {"wikidata_qid": qid, "slice": slice_name}
            inception = row.get("inception", {}).get("value")
            if inception and len(inception) >= 4 and inception[:4].isdigit():
                attributes["founded"] = int(inception[:4])
            website = row.get("website", {}).get("value")
            if website:
                attributes["website"] = website

            entities.append(
                {
                    "id": entity_id,
                    "entity_type": spec.entity_type,
                    "name": label,
                    "summary": row.get("description", {}).get("value"),
                    "attributes": attributes,
                    "aliases": aliases[:12],
                }
            )
            count += 1
        per_slice_counts[slice_name] = count

    relationships: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    dropped_rels = 0
    for prop, pairs in relation_pairs.items():
        rel_type, direction = RELATION_PROPS[prop]
        for item_qid, value_qid in pairs:
            if item_qid not in id_by_qid or value_qid not in id_by_qid:
                dropped_rels += 1
                continue
            source_qid, target_qid = (
                (value_qid, item_qid) if direction == "reverse" else (item_qid, value_qid)
            )
            edge = (id_by_qid[source_qid], id_by_qid[target_qid], rel_type)
            if edge[0] == edge[1] or edge in seen_edges:
                continue
            seen_edges.add(edge)
            relationships.append({"source": edge[0], "target": edge[1], "rel_type": rel_type})

    payload = {
        "source": "wikidata",
        "license": "CC0-1.0",
        "slices": [
            {
                "name": s.name,
                "wikidata_class": s.wikidata_class,
                "entity_type": s.entity_type,
                "min_sitelinks": s.min_sitelinks,
                "description": s.description,
                "entities": per_slice_counts.get(s.name, 0),
            }
            for s in slices
        ],
        "entities": entities,
        "relationships": relationships,
    }
    return ImportResult(
        payload=payload,
        stats={
            "entities": len(entities),
            "relationships": len(relationships),
            "aliases": sum(len(e["aliases"]) for e in entities),
            "per_slice": per_slice_counts,
            "relation_pairs_outside_import": dropped_rels,
        },
    )


def fetch_all(
    client: httpx.Client, slices: list[Slice], limit_per_slice: int
) -> tuple[dict[str, list[dict]], dict[str, list[tuple[str, str]]]]:
    slice_rows: dict[str, list[dict]] = {}
    all_qids: list[str] = []
    for spec in slices:
        query = ENTITY_QUERY.format(
            wikidata_class=spec.wikidata_class,
            min_sitelinks=spec.min_sitelinks,
            limit=limit_per_slice,
        )
        rows = sparql(client, query)
        slice_rows[spec.name] = rows
        all_qids.extend(_qid(r["item"]["value"]) for r in rows)
        print(f"  slice {spec.name}: {len(rows)} rows", file=sys.stderr)
        time.sleep(1)  # be polite to WDQS

    unique_qids = list(dict.fromkeys(all_qids))
    relation_pairs: dict[str, list[tuple[str, str]]] = {p: [] for p in RELATION_PROPS}
    chunk_size = 400
    for prop in RELATION_PROPS:
        for start in range(0, len(unique_qids), chunk_size):
            chunk = unique_qids[start : start + chunk_size]
            values = " ".join(f"wd:{q}" for q in chunk)
            rows = sparql(client, RELATION_QUERY.format(qids=values, prop=prop))
            relation_pairs[prop].extend(
                (_qid(r["item"]["value"]), _qid(r["value"]["value"]))
                for r in rows
                if r["value"]["value"].startswith("http://www.wikidata.org/entity/Q")
            )
            time.sleep(0.5)
        print(f"  property {prop}: {len(relation_pairs[prop])} pairs", file=sys.stderr)
    return slice_rows, relation_pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import scoped Wikidata slices as seed data.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit-per-slice", type=int, default=4000)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    with httpx.Client(timeout=args.timeout) as client:
        slice_rows, relation_pairs = fetch_all(client, SLICES, args.limit_per_slice)
    result = build_seed_payload(slice_rows, relation_pairs, SLICES)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(result.payload, ensure_ascii=False).encode()
    if args.out.suffix == ".gz":
        args.out.write_bytes(gzip.compress(raw, mtime=0))
    else:
        args.out.write_bytes(raw)
    print(json.dumps(result.stats, indent=2))
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
