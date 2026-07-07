"""End-to-end test: spawn the real MCP server over stdio and drive every tool
through an MCP ClientSession, proving the MVP is genuinely runnable."""

import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _payload(result) -> dict:
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


async def test_full_scenario(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemosyne.mcp_server"],
        env={
            "MNEMOSYNE_DATA_DIR": str(tmp_path),
            "MNEMOSYNE_ASSISTANT_ID": "e2e-fallback",
            "MNEMOSYNE_SEED_BASE_ONLY": "1",
        },
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, client_info=None) as session,
    ):
        await session.initialize()

        tools = {t.name for t in (await session.list_tools()).tools}
        assert tools == {
            "remember",
            "recall",
            "resolve_entity",
            "create_entity",
            "assert_relationship",
            "query_graph",
            "get_entity_timeline",
            "list_ontology",
            "review_proposals",
        }

        # 1. Ontology discovery
        ontology = _payload(await session.call_tool("list_ontology", {}))
        assert any(t["name"] == "works_at" for t in ontology["relationship_types"])

        # 2. Alias resolution against the auto-seeded canonical tier
        resolved = _payload(await session.call_tool("resolve_entity", {"name": "DBX"}))
        assert resolved["status"] == "matched"
        assert resolved["entity"]["id"] == "canon:company/databricks"
        assert resolved["method"] == "alias"

        # 3. Create the user as a private entity
        jeremy = _payload(
            await session.call_tool(
                "create_entity", {"name": "Jeremy Nelson", "entity_type": "person"}
            )
        )
        assert jeremy["id"].startswith("usr_")

        # 4. Remember a fact linking private + canonical entities
        memory = _payload(
            await session.call_tool(
                "remember",
                {
                    "content": "Jeremy is evaluating DBX for the lakehouse migration",
                    "entities": ["Jeremy Nelson", "DBX"],
                    "confidence": 0.9,
                },
            )
        )
        linked = {e["entity"]["id"] for e in memory["linked_entities"]}
        assert jeremy["id"] in linked
        assert "canon:company/databricks" in linked

        # 5. Temporal supersession of a functional relationship
        first = _payload(
            await session.call_tool(
                "assert_relationship",
                {
                    "source": "Jeremy Nelson",
                    "target": "Databricks",
                    "rel_type": "works_at",
                    "valid_from": "2024-01-01T00:00:00+00:00",
                },
            )
        )
        assert first["superseded"] == []
        second = _payload(
            await session.call_tool(
                "assert_relationship",
                {
                    "source": "Jeremy Nelson",
                    "target": "Anthropic",
                    "rel_type": "works_at",
                    "valid_from": "2025-06-01T00:00:00+00:00",
                },
            )
        )
        assert second["superseded"] == [first["relationship"]["id"]]

        # 6. Merged two-tier graph query with as_of
        graph_now = _payload(await session.call_tool("query_graph", {"entity": "Jeremy Nelson"}))
        targets_now = {e["target_name"] for e in graph_now["edges"]}
        assert "Anthropic" in targets_now
        assert "Databricks" not in targets_now  # superseded edge hidden

        graph_2024 = _payload(
            await session.call_tool(
                "query_graph",
                {"entity": "Jeremy Nelson", "as_of": "2024-07-01T00:00:00+00:00"},
            )
        )
        targets_2024 = {e["target_name"] for e in graph_2024["edges"]}
        assert "Databricks" in targets_2024

        # Canonical edges appear (tier-tagged) around Databricks
        dbx_graph = _payload(await session.call_tool("query_graph", {"entity": "DBX"}))
        tiers = {e["tier"] for e in dbx_graph["edges"]}
        assert "canonical" in tiers

        # 7. Recall by text
        recalled = _payload(await session.call_tool("recall", {"query": "lakehouse migration"}))
        assert recalled["memories"][0]["memory"]["id"] == memory["memory_id"]

        # 8. Timeline shows supersession with assistant provenance
        timeline = _payload(
            await session.call_tool("get_entity_timeline", {"entity": "Jeremy Nelson"})
        )
        kinds = [e["kind"] for e in timeline["events"]]
        assert "entity_created" in kinds
        assert "relationship_superseded" in kinds
        provs = {e["provenance"].get("source_type") for e in timeline["events"] if e["provenance"]}
        assert "assistant" in provs

        # Seed provenance is distinguishable on canonical entities
        dbx_timeline = _payload(
            await session.call_tool("get_entity_timeline", {"entity": "Databricks"})
        )
        dbx_provs = {
            e["provenance"].get("source_type") for e in dbx_timeline["events"] if e["provenance"]
        }
        assert "seed" in dbx_provs

        # 9. Proposals surface for mid-confidence mentions
        proposed = _payload(await session.call_tool("resolve_entity", {"name": "Datbrcks"}))
        assert proposed["status"] == "proposed"
        proposals = _payload(await session.call_tool("review_proposals", {"action": "list"}))
        assert any(p["candidate_name"] == "Datbrcks" for p in proposals["proposals"])
        accepted = _payload(
            await session.call_tool(
                "review_proposals",
                {"action": "accept", "proposal_id": proposed["proposal_id"]},
            )
        )
        assert accepted["proposal"]["status"] == "accepted"
        re_resolved = _payload(await session.call_tool("resolve_entity", {"name": "Datbrcks"}))
        assert re_resolved["status"] == "matched"
        assert re_resolved["method"] == "alias"
