# Show HN draft

**Title:** Mnemosyne — open-source, ontology-native memory for AI assistants (MCP)

**Body:**

I built Mnemosyne because every AI assistant I use forgets everything the moment
the session ends — and what little "memory" exists is text fragments in a vector
store, siloed per vendor, with no concept of identity, time, or trust.

Mnemosyne is a structured memory fabric: a semantic graph of canonical entities,
typed relationships, temporal state, and per-fact provenance. It sits behind the
Model Context Protocol (MCP), so Claude, Cursor, VS Code, and any other MCP
client share one memory — switch models and nothing is lost.

**What makes it different from RAG / vector memory:**

- **Identity resolution that learns.** "DBX", "the lakehouse vendor", and the
  typo "Databrics" all resolve to `canon:company/databricks`. High-confidence
  fuzzy matches are auto-learned as aliases; ambiguous ones become reviewable
  proposals instead of silent links.
- **Facts supersede, never overwrite.** Every fact has a validity window. New
  truths close old ones. "Who was the account owner in Q2?" is a query, not an
  apology.
- **Per-fact provenance.** Which assistant said it, when, at what confidence —
  from the protocol handshake, never self-reported.
- **Two-tier privacy.** A hosted shared ontology (read-only) and a local private
  overlay, merged at query time. The boundary is structural — a test asserts the
  shared database is byte-identical after a full write workload.

**Try it in 60 seconds:**

```bash
git clone https://github.com/jerednel/mnemosyne.git && cd mnemosyne
uv sync
uv run python examples/demo.py
```

Wire it into Claude Code:

```bash
claude mcp add mnemosyne -- uv run --directory /path/to/mnemosyne mnemosyne-server
```

Ships with ~4,800 canonical entities from Wikidata (CC0) — tech companies,
languages, frameworks, databases. A hosted canonical tier is live at
https://mnemosyne-production-ed7b.up.railway.app (public `/v1/health`).

74 tests, MIT licensed, Python 3.11+, no heavy dependencies.

GitHub: https://github.com/jerednel/mnemosyne
