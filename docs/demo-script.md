# Demo Script — 2-Minute Video

Target: show identity resolution, temporal supersession, cross-assistant
provenance, and the live hosted tier in a tight sequence. Record in a clean
terminal with a dark theme; no slides.

## Shot list

### 1. Identity resolution (0:00–0:30)

Open a terminal. Run the MCP server or `examples/demo.py`.

- **Resolve "DBX"** → `canon:company/databricks` (alias, confidence 100).
  Call out: stock tickers, abbreviations, and nicknames resolve to canonical
  entities out of the box.
- **Resolve "sqllite"** (typo) → `canon:technology/sqlite` (fuzzy, confidence
  95, alias learned). Call out: the typo is now a learned alias — next time it
  resolves instantly at 100.
- **Resolve "sqllite" again** → confidence 100, method "alias". The fabric
  learned.

### 2. Temporal supersession (0:30–1:00)

- **Assert `works_at` Jeremy → Databricks**, `valid_from 2024-01`.
- **Assert `works_at` Jeremy → Anthropic**, `valid_from 2025-06`.
  Show the response: "superseded 1 prior fact — history preserved."
- **Query employer `as_of 2024-07`** → Databricks.
- **Query employer today** → Anthropic.
  Call out: old facts aren't deleted — they're closed with `valid_to` and
  `superseded_by`. The full history is always queryable.

### 3. Cross-assistant provenance (1:00–1:30)

- **Get entity timeline for Jeremy.** Show two provenance rows: one from
  `claude-code`, one from `cursor`. Call out: each fact knows which assistant
  asserted it, from the MCP handshake — never self-reported. Two assistants
  contributed to one coherent record.

### 4. Live hosted tier (1:30–2:00)

- **`curl https://mnemosyne-production-ed7b.up.railway.app/v1/health`**
  Show the JSON response: entity count, health status.
  Call out: the shared ontology is hosted and authenticated. Any client points
  at it with two env vars — private overlays stay local.
- Close with: "4,800 entities from Wikidata, MIT licensed, 74 tests.
  `git clone`, `uv sync`, and you're running. Link in the description."
