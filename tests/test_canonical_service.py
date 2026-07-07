"""Server-side tests for the hosted canonical tier: auth, parity, errors."""

import pytest
from conftest import API_KEYS

from mnemosyne.canonical_service import create_app, keys_from_env

AUTH = {"Authorization": "Bearer test-secret"}


def test_health_requires_no_auth(service_client):
    response = service_client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["seeded_at"] is not None


def test_missing_auth_rejected(service_client):
    response = service_client.get("/v1/entities")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "detail" in response.json()


def test_wrong_secret_rejected(service_client):
    response = service_client.get("/v1/entities", headers={"Authorization": "Bearer wrong-secret"})
    assert response.status_code == 401


def test_valid_keys_identified_per_tenant(service_client):
    for key_id, secret in API_KEYS.items():
        response = service_client.get("/v1/entities", headers={"Authorization": f"Bearer {secret}"})
        assert response.status_code == 200
        assert response.headers["X-Mnemosyne-Key-Id"] == key_id


def test_fails_closed_without_keys(canonical_store):
    with pytest.raises(ValueError, match="fail closed"):
        create_app(canonical_store, {})


def test_keys_from_env_pairs(monkeypatch):
    monkeypatch.delenv("MNEMOSYNE_API_KEYS_FILE", raising=False)
    monkeypatch.setenv("MNEMOSYNE_API_KEYS", "jeremy:mk_abc, ci:mk_def")
    assert keys_from_env() == {"jeremy": "mk_abc", "ci": "mk_def"}
    monkeypatch.setenv("MNEMOSYNE_API_KEYS", "malformed")
    with pytest.raises(ValueError, match="key_id:secret"):
        keys_from_env()


def test_keys_from_env_file_takes_precedence(monkeypatch, tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text('{"filekey": "file-secret"}')
    monkeypatch.setenv("MNEMOSYNE_API_KEYS", "envkey:env-secret")
    monkeypatch.setenv("MNEMOSYNE_API_KEYS_FILE", str(keys_file))
    assert keys_from_env() == {"filekey": "file-secret"}


def test_endpoint_parity_with_direct_store(service_client, canonical_store):
    """Every endpoint returns exactly what the direct store call returns."""
    dbx = "canon:company/databricks"

    entity = service_client.get("/v1/entities/get", params={"id": dbx}, headers=AUTH).json()
    assert entity == canonical_store.get_entity(dbx).model_dump(mode="json")

    by_name = service_client.get(
        "/v1/entities/by-name", params={"name": "databricks"}, headers=AUTH
    ).json()["items"]
    assert by_name == [
        e.model_dump(mode="json") for e in canonical_store.find_by_normalized_name("databricks")
    ]

    all_entities = service_client.get("/v1/entities", headers=AUTH).json()["items"]
    assert len(all_entities) == len(canonical_store.all_entities())
    companies = service_client.get("/v1/entities", params={"type": "company"}, headers=AUTH).json()[
        "items"
    ]
    assert {e["id"] for e in companies} == {e.id for e in canonical_store.all_entities("company")}

    aliases = service_client.get(
        "/v1/aliases/by-name", params={"name": "dbx"}, headers=AUTH
    ).json()["items"]
    assert aliases == [a.model_dump(mode="json") for a in canonical_store.find_aliases("dbx")]

    for_entity = service_client.get(
        "/v1/aliases/for-entity", params={"entity_id": dbx}, headers=AUTH
    ).json()["items"]
    assert for_entity == [a.model_dump(mode="json") for a in canonical_store.aliases_for(dbx)]

    all_aliases = service_client.get("/v1/aliases", headers=AUTH).json()["items"]
    assert len(all_aliases) == len(canonical_store.all_aliases())

    rels = service_client.get("/v1/relationships", params={"entity_id": dbx}, headers=AUTH).json()[
        "items"
    ]
    assert rels == [r.model_dump(mode="json") for r in canonical_store.relationships_for(dbx)]

    prov_id = canonical_store.get_entity(dbx).provenance_id
    provenance = service_client.get(
        "/v1/provenance/get", params={"id": prov_id}, headers=AUTH
    ).json()
    assert provenance == canonical_store.get_provenance(prov_id).model_dump(mode="json")

    assertions = service_client.get(
        "/v1/assertions", params={"subject_id": dbx}, headers=AUTH
    ).json()["items"]
    assert assertions == [a.model_dump(mode="json") for a in canonical_store.assertions_for(dbx)]


def test_unknown_id_404(service_client):
    response = service_client.get(
        "/v1/entities/get", params={"id": "canon:company/nope"}, headers=AUTH
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Entity not found"


def test_missing_param_400(service_client):
    response = service_client.get("/v1/entities/get", headers=AUTH)
    assert response.status_code == 400
    assert "Missing query parameter" in response.json()["detail"]


def test_site_served_when_configured(canonical_db, tmp_path):
    from pathlib import Path

    from starlette.testclient import TestClient

    from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<h1>Mnemosyne marketing</h1>")
    store = SqliteCanonicalStore(canonical_db, check_same_thread=False)
    app = create_app(store, dict(API_KEYS), site_dir=Path(site_dir))
    with TestClient(app) as client:
        # Site at "/" is public...
        home = client.get("/")
        assert home.status_code == 200
        assert "Mnemosyne marketing" in home.text
        # ...while the API keeps precedence and stays authenticated.
        assert client.get("/v1/entities").status_code == 401
        assert client.get("/v1/health").status_code == 200
        entity = client.get(
            "/v1/entities/get", params={"id": "canon:company/databricks"}, headers=AUTH
        )
        assert entity.status_code == 200
    store.close()


def test_no_site_dir_leaves_root_unrouted(service_client):
    assert service_client.get("/").status_code == 404
