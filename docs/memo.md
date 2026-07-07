# Mnemosyne — Investor Memo

## The problem

Every AI assistant today is a brilliant intern with retrograde amnesia. Memory is
vector search over chat logs: text fragments with no canonical identity, no
relationships, no sense of time, siloed per vendor. When an organization runs
Claude in one tool and Copilot in another, nothing learned in one is available to
the other — and switching models means starting over. The cost of re-explaining
context compounds across every assistant, every session, every team.

## Thesis

Models are becoming interchangeable reasoning engines. The durable competitive
layer is **memory, identity, and continuity** — the structured substrate that
persists across model swaps, tool changes, and organizational growth. Whoever
owns that layer owns the switching cost.

## What's built

Mnemosyne is an **ontology-native memory fabric** for AI assistants: a semantic
graph of canonical entities, typed relationships, temporal state, and per-fact
provenance, exposed through the open Model Context Protocol (MCP).

**Live today:**

- Two-tier architecture: a hosted shared ontology (read-only canonical tier) and
  a local-first private overlay, merged at query time. The privacy boundary is
  structural — no code path exists from overlay writes to canonical storage,
  verified by automated byte-identity tests.
- **4,784 canonical entities** and **4,961 aliases** imported from scoped
  Wikidata slices (CC0), covering software/technology companies, programming
  languages, databases, frameworks, and major platforms. "MSFT" → Microsoft,
  "K8s" → Kubernetes, and the typo "sqllite" → SQLite all resolve out of the box.
- Learning identity resolution: mentions resolve through normalize → exact →
  alias → fuzzy stages. High-confidence fuzzy hits are accepted *and recorded as
  learned aliases*; ambiguous hits become reviewable merge proposals.
  Pluggable matcher seam supports embedding backends (OpenAI, Voyage) over
  lexical matching.
- Temporal supersession: facts carry `valid_from`/`valid_to`/`superseded_by`.
  New truths close old ones; `as_of` queries answer "what was true in July 2024?"
  Full history lives in an append-only assertion log written atomically with
  every state change.
- Per-fact provenance: asserting assistant (from MCP handshake, never
  self-reported), session, timestamp, confidence, and derivation path.
- 9-tool MCP stdio server, compatible with Claude Code, Claude Desktop, Cursor,
  VS Code, and any MCP client.
- Hosted canonical tier: Starlette service with bearer-key multi-tenant auth,
  live at https://mnemosyne-production-ed7b.up.railway.app (`GET /v1/health`
  is public). `HttpCanonicalStore` is a drop-in remote implementation of the
  same `CanonicalStore` Protocol — point any client at a hosted tier with two
  env vars.
- 74 automated tests: stdio MCP end-to-end, hosted-tier HTTP, cross-assistant
  continuity, canonical isolation, temporal supersession, identity resolution.
- MIT licensed, Python 3.11+, three lightweight dependencies.

## Architecture

```
┌──────────────── any MCP client (Claude, Cursor, VS Code, …) ────────────────┐
│  remember · recall · resolve_entity · assert_relationship · query_graph · …  │
└─────────────────────────────────┬────────────────────────────────────────────┘
                           MemoryFabric (merge + identity resolution)
                     ┌────────────┴─────────────┐
         Tier 1: Canonical ontology    Tier 2: Private overlay
         shared · hosted · read-only   local-first · read/write
         (SQLite today → Postgres)     entities, edges, memories,
                                       learned aliases, proposals
```

## Moat

1. **Learning identity resolution.** The more assistants use the fabric, the
   better it resolves — learned aliases and relationship patterns accumulate as a
   flywheel that cannot be replicated by dumping a database.
2. **User-owned accumulating graph.** Every fact, every supersession, every
   provenance record stays in the user's possession. Switching *away* means
   losing an asset that grew with use — the kind of lock-in that users choose
   rather than resent.
3. **Protocol-level interop.** MCP is the surface. Mnemosyne doesn't compete
   with assistants — it makes all of them better, which means distribution rides
   on every MCP client's adoption.

## Business model

**Hosted canonical tier** — the Tailscale-style motion:

- Open-source client and local overlay are free forever (MIT).
- The hosted canonical tier (shared ontology, managed identity resolution,
  multi-tenant auth, upcoming Postgres backend) is the paid product. Free for
  individuals; team/org plans by seat and entity volume.
- Enterprise features on the roadmap: scoped permissions, cross-device sync,
  inference and decay policies, SLA-backed uptime.

The free tier seeds adoption across individual developers and small teams; the
paid tier captures value when organizations need shared context, audit trails,
and managed infrastructure.

## The ask

Pre-seed round to fund:

1. Postgres canonical backend and multi-tenant SaaS infrastructure.
2. Cross-device / cross-assistant sync (the append-only assertion log is
   already replayable and mergeable by design).
3. First paid team deployments and developer advocacy.
