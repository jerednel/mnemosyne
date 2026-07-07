"""Remote canonical tier: an HTTP client implementing the CanonicalStore
Protocol against the hosted canonical service. Drop-in for SqliteCanonicalStore
— nothing above the storage layer changes.

Canonical data changes rarely, so reads are cached with a TTL; the fuzzy
resolution hot paths (all_entities/all_aliases) hit the cache in steady state."""

from __future__ import annotations

import time
from typing import Any

import httpx

from mnemosyne.models import Alias, Assertion, Entity, Provenance, Relationship


class CanonicalServiceError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Canonical service error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple) -> Any | None:
        if self.ttl <= 0:
            return None
        hit = self._data.get(key)
        if hit is None or hit[0] < time.monotonic():
            return None
        return hit[1]

    def put(self, key: tuple, value: Any) -> None:
        if self.ttl > 0:
            self._data[key] = (time.monotonic() + self.ttl, value)


class HttpCanonicalStore:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.Client | None = None,
        cache_ttl: float = 300.0,
    ):
        self._owns_client = client is None
        # Relative request paths, so an injected starlette TestClient (base_url
        # http://testserver) works unmodified.
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._cache = _TTLCache(cache_ttl)

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any | None:
        response = self._client.get(path, params=params, headers=self._headers)
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise CanonicalServiceError(401, "Unauthorized — check MNEMOSYNE_CANONICAL_API_KEY")
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            raise CanonicalServiceError(response.status_code, detail)
        return response.json()

    def _cached(self, key: tuple, path: str, params: dict[str, str] | None = None) -> Any | None:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._get(path, params)
        if value is not None:
            self._cache.put(key, value)
        return value

    def _cached_items(self, key: tuple, path: str, params: dict[str, str] | None, model) -> list:
        payload = self._cached(key, path, params)
        return [model.model_validate(item) for item in (payload or {}).get("items", [])]

    # ------------------------------------------------------------------
    # CanonicalStore Protocol
    # ------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> Entity | None:
        payload = self._cached(("get_entity", entity_id), "/v1/entities/get", {"id": entity_id})
        return Entity.model_validate(payload) if payload else None

    def find_by_normalized_name(self, normalized_name: str) -> list[Entity]:
        return self._cached_items(
            ("by_name", normalized_name),
            "/v1/entities/by-name",
            {"name": normalized_name},
            Entity,
        )

    def find_aliases(self, normalized_alias: str) -> list[Alias]:
        return self._cached_items(
            ("aliases_by_name", normalized_alias),
            "/v1/aliases/by-name",
            {"name": normalized_alias},
            Alias,
        )

    def aliases_for(self, entity_id: str) -> list[Alias]:
        return self._cached_items(
            ("aliases_for", entity_id),
            "/v1/aliases/for-entity",
            {"entity_id": entity_id},
            Alias,
        )

    def all_aliases(self) -> list[Alias]:
        return self._cached_items(("all_aliases",), "/v1/aliases", None, Alias)

    def all_entities(self, entity_type: str | None = None) -> list[Entity]:
        params = {"type": entity_type} if entity_type else None
        return self._cached_items(("all_entities", entity_type), "/v1/entities", params, Entity)

    def relationships_for(self, entity_id: str) -> list[Relationship]:
        return self._cached_items(
            ("relationships", entity_id),
            "/v1/relationships",
            {"entity_id": entity_id},
            Relationship,
        )

    def get_provenance(self, provenance_id: str) -> Provenance | None:
        payload = self._cached(
            ("provenance", provenance_id), "/v1/provenance/get", {"id": provenance_id}
        )
        return Provenance.model_validate(payload) if payload else None

    def assertions_for(self, subject_id: str) -> list[Assertion]:
        return self._cached_items(
            ("assertions", subject_id),
            "/v1/assertions",
            {"subject_id": subject_id},
            Assertion,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
