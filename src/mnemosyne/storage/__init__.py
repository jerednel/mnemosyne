"""Storage backends and the env-driven canonical-store factory."""

from __future__ import annotations

import os

from mnemosyne.storage.base import CanonicalStore


def canonical_store_from_env() -> CanonicalStore:
    """Local SQLite canonical tier by default; a remote hosted tier when
    MNEMOSYNE_CANONICAL_URL is set. Nothing above the storage layer changes."""
    url = os.environ.get("MNEMOSYNE_CANONICAL_URL")
    if url:
        from mnemosyne.storage.http_canonical import HttpCanonicalStore

        return HttpCanonicalStore(
            base_url=url,
            api_key=os.environ.get("MNEMOSYNE_CANONICAL_API_KEY", ""),
            cache_ttl=float(os.environ.get("MNEMOSYNE_CANONICAL_CACHE_TTL", "300")),
        )

    from mnemosyne.config import canonical_db_path
    from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore

    path = canonical_db_path()
    if not path.exists():
        from mnemosyne.seed.loader import build_canonical_db

        build_canonical_db(path)
    return SqliteCanonicalStore(path)
