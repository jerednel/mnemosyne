"""Hosted canonical tier: an authenticated, read-only HTTP service serving the
shared ontology. One endpoint per CanonicalStore Protocol method, so the
HttpCanonicalStore client is a drop-in remote implementation of the same
interface.

Entity ids contain ':' and '/' (canon:company/databricks), so ids are always
passed as query parameters, never as URL path segments.

    MNEMOSYNE_API_KEYS=jeremy:mk_secret uv run mnemosyne-canonical
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from mnemosyne.config import canonical_db_path
from mnemosyne.storage.base import CanonicalStore
from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore

logger = logging.getLogger("mnemosyne.canonical")

DEFAULT_PORT = 8321


def keys_from_env() -> dict[str, str]:
    """Load API keys: MNEMOSYNE_API_KEYS_FILE (JSON {key_id: secret}) takes
    precedence over MNEMOSYNE_API_KEYS (comma-separated key_id:secret pairs)."""
    keys_file = os.environ.get("MNEMOSYNE_API_KEYS_FILE")
    if keys_file:
        return json.loads(Path(keys_file).read_text())
    keys: dict[str, str] = {}
    for pair in os.environ.get("MNEMOSYNE_API_KEYS", "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key_id, _, secret = pair.partition(":")
        if not key_id or not secret:
            raise ValueError(
                f"MNEMOSYNE_API_KEYS entries must be key_id:secret pairs, got {pair!r}"
            )
        keys[key_id] = secret
    return keys


def create_app(
    store: CanonicalStore, keys: dict[str, str], site_dir: Path | None = None
) -> Starlette:
    if not keys:
        raise ValueError("Refusing to serve without API keys (fail closed).")

    def require_key(request: Request) -> str:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=401,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        for key_id, secret in keys.items():
            if secrets.compare_digest(token, secret):
                request.state.key_id = key_id
                logger.info("key=%s path=%s", key_id, request.url.path)
                return key_id
        raise HTTPException(
            status_code=401,
            detail="Unknown API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def param(request: Request, name: str) -> str:
        value = request.query_params.get(name)
        if not value:
            raise HTTPException(status_code=400, detail=f"Missing query parameter {name!r}")
        return value

    def respond(payload, key_id: str, status_code: int = 200) -> JSONResponse:
        return JSONResponse(
            payload, status_code=status_code, headers={"X-Mnemosyne-Key-Id": key_id}
        )

    def items(models_list, key_id: str) -> JSONResponse:
        return respond({"items": [m.model_dump(mode="json") for m in models_list]}, key_id)

    def health(request: Request) -> JSONResponse:
        seeded_at = None
        counts = {}
        conn = getattr(store, "conn", None)
        if conn is not None:
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'seeded_at'").fetchone()
            seeded_at = row[0] if row else None
            for table in ("entities", "aliases", "relationships"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        return JSONResponse({"status": "ok", "seeded_at": seeded_at, **counts})

    def get_entity(request: Request) -> JSONResponse:
        key_id = require_key(request)
        entity = store.get_entity(param(request, "id"))
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        return respond(entity.model_dump(mode="json"), key_id)

    def entities_by_name(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.find_by_normalized_name(param(request, "name")), key_id)

    def all_entities(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.all_entities(request.query_params.get("type") or None), key_id)

    def aliases_by_name(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.find_aliases(param(request, "name")), key_id)

    def aliases_for_entity(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.aliases_for(param(request, "entity_id")), key_id)

    def all_aliases(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.all_aliases(), key_id)

    def relationships(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.relationships_for(param(request, "entity_id")), key_id)

    def get_provenance(request: Request) -> JSONResponse:
        key_id = require_key(request)
        provenance = store.get_provenance(param(request, "id"))
        if provenance is None:
            raise HTTPException(status_code=404, detail="Provenance not found")
        return respond(provenance.model_dump(mode="json"), key_id)

    def assertions(request: Request) -> JSONResponse:
        key_id = require_key(request)
        return items(store.assertions_for(param(request, "subject_id")), key_id)

    def http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    routes: list = [
        Route("/v1/health", health),
        Route("/v1/entities/get", get_entity),
        Route("/v1/entities/by-name", entities_by_name),
        Route("/v1/entities", all_entities),
        Route("/v1/aliases/by-name", aliases_by_name),
        Route("/v1/aliases/for-entity", aliases_for_entity),
        Route("/v1/aliases", all_aliases),
        Route("/v1/relationships", relationships),
        Route("/v1/provenance/get", get_provenance),
        Route("/v1/assertions", assertions),
    ]
    if site_dir is not None:
        # Marketing site at "/" (unauthenticated static files). Mounted after
        # the /v1 routes so the API always takes precedence.
        routes.append(Mount("/", app=StaticFiles(directory=str(site_dir), html=True)))

    return Starlette(routes=routes, exception_handlers={HTTPException: http_exception})


def app_from_env(db_path: Path | None = None) -> Starlette:
    path = db_path or Path(os.environ.get("MNEMOSYNE_CANONICAL_DB") or canonical_db_path())
    if not path.exists():
        from mnemosyne.seed.loader import build_canonical_db

        build_canonical_db(path)
    store = SqliteCanonicalStore(path, check_same_thread=False)
    site_raw = os.environ.get("MNEMOSYNE_SITE_DIR")
    site_dir = Path(site_raw) if site_raw and Path(site_raw).is_dir() else None
    return create_app(store, keys_from_env(), site_dir=site_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Mnemosyne canonical ontology tier.")
    # PaaS convention (Railway, Heroku, ...): PORT is set and the service must
    # bind all interfaces; locally we default to loopback on 8321.
    paas_port = os.environ.get("PORT")
    default_host = "0.0.0.0" if paas_port else "127.0.0.1"  # noqa: S104
    parser.add_argument("--host", default=os.environ.get("MNEMOSYNE_CANONICAL_HOST", default_host))
    parser.add_argument(
        "--port",
        type=int,
        default=int(paas_port or os.environ.get("MNEMOSYNE_CANONICAL_PORT", DEFAULT_PORT)),
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    try:
        app = app_from_env(args.db)
    except ValueError as exc:
        print(f"{exc} Set MNEMOSYNE_API_KEYS or MNEMOSYNE_API_KEYS_FILE.", file=sys.stderr)
        return 1
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
