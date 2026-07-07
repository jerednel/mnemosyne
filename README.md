# Mnemosyne

**An ontology-native memory fabric for AI assistants.**

Mnemosyne is the structured memory layer that sits between LLMs and human context: a
semantic graph of canonical entities, typed relationships, temporal state, and
provenance that any assistant can read and write through [MCP](https://modelcontextprotocol.io).
Models are becoming interchangeable reasoning engines — the durable layer is memory,
identity, and continuity. Mnemosyne is that layer.

```
"DBX" ─┐
"the lakehouse vendor" ─┼──▶ canon:company/databricks ──competitor_of──▶ Snowflake
"Databrics" (learned) ──┘        ▲ works_at (2024-01 → 2025-06, superseded)
                                 │ works_at (2025-06 → now)
                            Jeremy Nelson
```

## Why this exists

Today's assistant memory is vector search over chat logs: text fragments, no canonical
identity, no relationships, no sense of time, siloed per assistant. Every assistant is
a brilliant intern with retrograde amnesia. Mnemosyne replaces retrieval-first memory
with **structure-first memory**:

| | Vector memory | Enterprise knowledge graphs | **Mnemosyne** |
|---|---|---|---|
| Identity | text similarity | canonical, but org-owned | canonical **+ private overlay** |
| Time | overwrite/append text | mostly static | validity windows + supersession |
| Trust | none | curated | per-fact provenance & confidence |
| Assistant access | per-vendor silo | none | any MCP client, model-agnostic |

## Architecture: two tiers, one graph

The core design decision is a **hard boundary between shared knowledge and private
context**, merged at query time:

```
┌───────────────────────────── assistants (any MCP client) ─────────────────────────────┐
│   remember · recall · resolve_entity · assert_relationship · query_graph · timeline   │
└──────────────────────────────────────────┬────────────────────────────────────────────┘
                                    MemoryFabric (merge layer)
                          ┌────────────────┴─────────────────┐
              Tier 1: canonical ontology           Tier 2: private overlay
              shared · hosted-ready                local-first · read/write
              opened READ-ONLY at runtime          entities, edges, memories,
              (SQLite mode=ro today;               learned aliases, proposals —
              Postgres behind the same             may reference canonical ids;
              CanonicalStore Protocol)             never the reverse
```

- **The privacy boundary is structural, not policy.** The canonical tier is opened with
  SQLite `mode=ro`; there is no code path from the fabric to a canonical write. A test
  asserts the canonical file is *byte-identical* after a full write workload.
- **The canonical tier is hosted — not just hosted-ready.** `mnemosyne-canonical` serves
  the shared ontology over authenticated HTTP (bearer keys with per-key identity), and
  `HttpCanonicalStore` is a drop-in remote implementation of the same `CanonicalStore`
  Protocol: point any client at a hosted tier with two env vars
  (`MNEMOSYNE_CANONICAL_URL`, `MNEMOSYNE_CANONICAL_API_KEY`) and nothing above the
  storage layer changes. Postgres slots in behind the same Protocol later.
- **Overlay extends canonical.** A private entity can `extends` a canonical one:
  your notes, aliases ("that client"), and attributes merge over the shared record
  at query time without ever leaving your machine.

## What makes it different

**Identity resolution that learns.** Mentions resolve through normalize → exact →
alias → fuzzy stages. High-confidence fuzzy hits ("Databrics" @ 95) are accepted *and
recorded as learned aliases* — the fabric gets better at recognizing your shorthand
with use. Mid-confidence hits (80–91) become **merge proposals** instead of silent
links: the write still lands on a provisional entity, and the proposal is on record
for review. Memory pollution is a design constraint, not an afterthought. The matcher
is pluggable: set `MNEMOSYNE_EMBEDDINGS=openai:<model>` (or `voyage:<model>`) to layer
semantic similarity over lexical matching — paraphrases like "the ml notebooks vendor"
resolve where string distance alone cannot.

**Facts supersede; they don't overwrite.** Every entity, relationship, and memory has
`valid_from` / `valid_to` / `superseded_by`. Asserting a new `works_at` automatically
closes the previous one (functional relationships), and `as_of` queries answer *"what
was true in July 2024?"*. The full history is preserved in an append-only assertion
log written in the same transaction as every state change.

**Every fact knows where it came from.** Provenance rows capture the asserting
assistant (from the MCP handshake — never self-reported), session, server-side
timestamp, stated confidence, and derivation path. Seed knowledge, assistant
assertions, and inferences are permanently distinguishable.

**Model-agnostic by construction.** The only surface is MCP over stdio. Claude, IDE
agents, and local models share one cognition substrate; swapping models loses nothing.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jerednel/mnemosyne.git && cd mnemosyne
uv sync
uv run python examples/demo.py   # the whole story in 30 seconds
uv run pytest -q                 # 74 tests: stdio MCP e2e, hosted-tier HTTP, interop
```

Wire it into Claude Code:

```bash
claude mcp add mnemosyne -- uv run --directory /path/to/mnemosyne mnemosyne-server
```

Configs for Claude Desktop, Cursor, and VS Code — plus hosting the canonical tier for
a team — are in [docs/integrations.md](docs/integrations.md). On first run the server
seeds the canonical tier automatically. Data lives in `~/.mnemosyne/` (override with
`MNEMOSYNE_DATA_DIR`; identify non-MCP callers with `MNEMOSYNE_ASSISTANT_ID`).

Host the shared ontology for many machines:

```bash
uv run mnemosyne-seed
MNEMOSYNE_API_KEYS="you:mk_change_me" uv run mnemosyne-canonical   # port 8321
# any client: MNEMOSYNE_CANONICAL_URL=... MNEMOSYNE_CANONICAL_API_KEY=... mnemosyne-server
```

## Assistant API (MCP tools)

| Tool | What it does |
|---|---|
| `remember` | Record an observation, auto-linking mentioned entities |
| `recall` | Search memories by text/entity/kind, optionally `as_of` a moment |
| `resolve_entity` | Mention → canonical or private entity, with method + confidence |
| `create_entity` | Create a private entity, optionally `extends` a canonical one |
| `assert_relationship` | Typed edge with auto-supersession of functional facts |
| `query_graph` | Merged two-tier graph query with direction, depth, `as_of` |
| `get_entity_timeline` | Full append-only history of an entity, with provenance |
| `list_ontology` | Entity types + relationship taxonomy (assistants self-discover) |
| `review_proposals` | List/accept/reject pending identity-merge proposals |

## Ontology coverage

The canonical tier ships with **~4,800 entities and ~5,600 aliases** imported from
scoped Wikidata slices (CC0): software/technology companies, programming languages,
notable software, libraries, frameworks, operating systems, databases, and major
platforms — every imported fact traces to its Wikidata QID. "MSFT" → Microsoft,
"K8s" → Kubernetes, and the typo "sqllite" → SQLite all resolve out of the box.
Rebuild or extend with `mnemosyne-import`; the full breakdown is auto-generated in
[docs/ontology-coverage.md](docs/ontology-coverage.md).

## Data model

Both tiers share one schema (`storage/schema.py`): `entities` (stable ids —
`canon:company/databricks` vs `usr_<uuid>` — aliases, attributes, confidence),
`relationships` (typed, constrained by the ontology), `memories` (+FTS), `provenance`,
`merge_proposals`, and the append-only `assertions` event log. The seed ontology ships
10 entity types and 16 relationship types as data (JSON), not code — user-extensible
taxonomies need no code changes.

## Roadmap

- **Postgres canonical backend** — multi-tenant Postgres behind the existing
  `CanonicalStore` Protocol (the HTTP service and auth layer are already in place);
  community-governed ontology growth
- **Local embedding backend** — `mnemosyne[embeddings-local]` extra behind the existing
  `Matcher` seam; API-based backends (OpenAI/Voyage) already ship
- **Cross-device / cross-assistant sync** — the append-only assertion log is replayable
  and mergeable by design (two-assistant continuity is already proven in
  `tests/test_continuity_e2e.py`)
- **Scoped permissions** — per-assistant namespaces and revocable memory grants on the
  provenance foundation
- **Inference & decay** — derived facts (`source_type=inference` is already modeled)
  and read-time relevance scoring

## Deploying on Railway

The production instance runs at
https://mnemosyne-production-ed7b.up.railway.app. To reconnect the Railway
service to this repo (one-time, after the repo split from `jerednel/jerednel`):

1. Open the Railway dashboard → project "mnemosyne" → service settings.
2. Under **Source**, disconnect the old repo (`jerednel/jerednel`).
3. Connect the new repo (`jerednel/mnemosyne`), branch `main`.
4. Remove the **Root Directory** override (previously `mnemosyne`) — the
   Dockerfile is now at the repo root.
5. Verify existing environment variables are intact: `MNEMOSYNE_API_KEYS`,
   `MNEMOSYNE_SITE_DIR=/app/site`.
6. Trigger a manual deploy or push to `main` — Railway will build from
   `Dockerfile` at the root and health-check at `/v1/health`.

`railway.json` is already configured; no file changes are needed.

## License

MIT
