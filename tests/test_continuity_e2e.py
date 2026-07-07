"""Cross-assistant continuity proof: two different MCP clients share one
memory fabric. Assistant B sees assistant A's memories with A's provenance,
supersedes one of A's facts, and the timeline attributes each event to the
assistant that made it."""

import json
import sys
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _payload(result) -> dict:
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


@asynccontextmanager
async def assistant_session(data_dir, name: str, version: str):
    """One MCP stdio session identifying itself as a distinct assistant.
    Sessions run sequentially against the same data dir (shared fabric)."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemosyne.mcp_server"],
        env={"MNEMOSYNE_DATA_DIR": str(data_dir), "MNEMOSYNE_SEED_BASE_ONLY": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(
            read,
            write,
            client_info=types.Implementation(name=name, version=version),
        ) as session,
    ):
        await session.initialize()
        yield session


async def test_two_assistants_share_one_fabric(tmp_path):
    # --- Assistant A: Claude Code writes memory ---
    async with assistant_session(tmp_path, "claude-code", "2.0") as claude:
        await claude.call_tool("create_entity", {"name": "Jeremy Nelson", "entity_type": "person"})
        memory = _payload(
            await claude.call_tool(
                "remember",
                {
                    "content": "Jeremy prefers uv over pip for Python projects",
                    "entities": ["Jeremy Nelson"],
                    "kind": "preference",
                },
            )
        )
        first_job = _payload(
            await claude.call_tool(
                "assert_relationship",
                {
                    "source": "Jeremy Nelson",
                    "target": "Databricks",
                    "rel_type": "works_at",
                    "valid_from": "2024-01-01T00:00:00+00:00",
                },
            )
        )

    # --- Assistant B: a different client reads A's memory and evolves it ---
    async with assistant_session(tmp_path, "cursor", "1.5") as cursor:
        recalled = _payload(await cursor.call_tool("recall", {"query": "uv pip"}))
        assert recalled["memories"][0]["memory"]["id"] == memory["memory_id"]
        # B sees exactly which assistant asserted the fact — cross-assistant
        # provenance, not self-reported.
        assert recalled["memories"][0]["provenance"]["assistant_id"] == "claude-code/2.0"

        superseded = _payload(
            await cursor.call_tool(
                "assert_relationship",
                {
                    "source": "Jeremy Nelson",
                    "target": "Anthropic",
                    "rel_type": "works_at",
                    "valid_from": "2025-06-01T00:00:00+00:00",
                },
            )
        )
        assert superseded["superseded"] == [first_job["relationship"]["id"]]

        timeline = _payload(
            await cursor.call_tool("get_entity_timeline", {"entity": "Jeremy Nelson"})
        )
        by_assistant = {
            e["kind"]: e["provenance"].get("assistant_id")
            for e in timeline["events"]
            if e["provenance"]
        }
        # A created the entity; B superseded the employment fact.
        assert by_assistant["entity_created"] == "claude-code/2.0"
        assert by_assistant["relationship_superseded"] == "cursor/1.5"
        assistants_seen = {
            e["provenance"].get("assistant_id") for e in timeline["events"] if e["provenance"]
        }
        assert {"claude-code/2.0", "cursor/1.5"} <= assistants_seen

    # --- Assistant C: continuity survives yet another client ---
    async with assistant_session(tmp_path, "local-agent", "0.1") as local:
        graph = _payload(await local.call_tool("query_graph", {"entity": "Jeremy Nelson"}))
        targets = {e["target_name"] for e in graph["edges"]}
        assert "Anthropic" in targets  # current employer, asserted by B
        assert "Databricks" not in targets  # A's fact correctly superseded
