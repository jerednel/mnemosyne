"""Client-side tests: Protocol parity over HTTP, caching, fabric-over-HTTP,
and one live loopback round-trip through a real uvicorn server."""

import threading
import time

import pytest
from conftest import API_KEYS

from mnemosyne.canonical_service import create_app
from mnemosyne.fabric import MemoryFabric
from mnemosyne.storage.base import CanonicalStore
from mnemosyne.storage.http_canonical import CanonicalServiceError, HttpCanonicalStore
from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore


@pytest.fixture()
def http_store(service_client):
    return HttpCanonicalStore(
        "http://testserver", "test-secret", client=service_client, cache_ttl=0
    )


def test_implements_canonical_store_protocol(http_store):
    assert isinstance(http_store, CanonicalStore)


def test_method_parity_with_sqlite(http_store, canonical_store):
    dbx = "canon:company/databricks"
    assert http_store.get_entity(dbx) == canonical_store.get_entity(dbx)
    assert http_store.get_entity("canon:company/nope") is None
    assert http_store.find_by_normalized_name("databricks") == (
        canonical_store.find_by_normalized_name("databricks")
    )
    assert http_store.find_aliases("dbx") == canonical_store.find_aliases("dbx")
    assert http_store.aliases_for(dbx) == canonical_store.aliases_for(dbx)
    assert http_store.all_aliases() == canonical_store.all_aliases()
    assert http_store.all_entities() == canonical_store.all_entities()
    assert http_store.all_entities("company") == canonical_store.all_entities("company")
    assert http_store.relationships_for(dbx) == canonical_store.relationships_for(dbx)
    prov_id = canonical_store.get_entity(dbx).provenance_id
    assert http_store.get_provenance(prov_id) == canonical_store.get_provenance(prov_id)
    assert http_store.get_provenance("prov_nope") is None
    assert http_store.assertions_for(dbx) == canonical_store.assertions_for(dbx)


def test_bad_key_raises(service_client):
    store = HttpCanonicalStore(
        "http://testserver", "wrong-secret", client=service_client, cache_ttl=0
    )
    with pytest.raises(CanonicalServiceError, match="MNEMOSYNE_CANONICAL_API_KEY"):
        store.all_entities()


def test_ttl_cache_collapses_requests(canonical_db):
    calls = []
    served = SqliteCanonicalStore(canonical_db, check_same_thread=False)
    app = create_app(served, dict(API_KEYS))

    from starlette.testclient import TestClient

    class CountingClient(TestClient):
        def get(self, *args, **kwargs):
            calls.append(args[0])
            return super().get(*args, **kwargs)

    with CountingClient(app) as client:
        cached = HttpCanonicalStore(
            "http://testserver", "test-secret", client=client, cache_ttl=300
        )
        assert cached.all_entities() == cached.all_entities()
        assert calls.count("/v1/entities") == 1

        uncached = HttpCanonicalStore(
            "http://testserver", "test-secret", client=client, cache_ttl=0
        )
        uncached.all_entities()
        uncached.all_entities()
        assert calls.count("/v1/entities") == 3
    served.close()


def test_fabric_over_http(http_store, overlay_store, provenance):
    """The full fabric runs against a remote canonical tier: alias resolution,
    fuzzy resolution (all_entities/all_aliases over HTTP), and graph queries."""
    fabric = MemoryFabric(canonical=http_store, overlay=overlay_store)

    resolved = fabric.resolve_entity("DBX", provenance)
    assert resolved.status == "matched"
    assert resolved.entity.id == "canon:company/databricks"
    assert resolved.method == "alias"

    fuzzy = fabric.resolve_entity("Databrick", provenance)
    assert fuzzy.entity.id == "canon:company/databricks"

    fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    edges = fabric.query_graph("canon:company/databricks")
    tiers = {e.relationship.rel_type: e.tier for e in edges}
    assert tiers["works_at"] == "overlay"
    assert tiers["competitor_of"] == "canonical"
    seed_edge = next(e for e in edges if e.tier == "canonical")
    assert seed_edge.provenance_summary["source_type"] == "seed"


def test_live_uvicorn_round_trip(canonical_db, overlay_store, provenance):
    """One real-socket test: uvicorn on a loopback ephemeral port."""
    import uvicorn

    served = SqliteCanonicalStore(canonical_db, check_same_thread=False)
    app = create_app(served, {"live": "live-secret"})
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                pytest.fail("uvicorn did not start within 10s")
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        store = HttpCanonicalStore(f"http://127.0.0.1:{port}", "live-secret")
        try:
            fabric = MemoryFabric(canonical=store, overlay=overlay_store)
            resolved = fabric.resolve_entity("the lakehouse vendor", provenance)
            assert resolved.entity.id == "canon:company/databricks"
        finally:
            store.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        served.close()
