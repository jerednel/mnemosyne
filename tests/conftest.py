import pytest
from starlette.testclient import TestClient

from mnemosyne.canonical_service import create_app
from mnemosyne.fabric import MemoryFabric
from mnemosyne.models import Provenance
from mnemosyne.ontology import OntologyRegistry
from mnemosyne.seed.loader import build_canonical_db
from mnemosyne.storage.sqlite_canonical import SqliteCanonicalStore
from mnemosyne.storage.sqlite_overlay import SqliteOverlayStore


@pytest.fixture(scope="session")
def canonical_db(tmp_path_factory):
    # Base seed only: unit tests need a small, deterministic canonical tier
    # regardless of which extended imports (seed/data/) are present locally.
    path = tmp_path_factory.mktemp("canonical") / "canonical.db"
    build_canonical_db(path, extended_dir=None)
    return path


@pytest.fixture()
def canonical_store(canonical_db):
    store = SqliteCanonicalStore(canonical_db)
    yield store
    store.close()


@pytest.fixture()
def overlay_store(tmp_path):
    store = SqliteOverlayStore(tmp_path / "overlay.db")
    yield store
    store.close()


@pytest.fixture()
def fabric(canonical_store, overlay_store):
    return MemoryFabric(canonical_store, overlay_store, OntologyRegistry.load())


@pytest.fixture()
def provenance():
    return Provenance(source_type="assistant", assistant_id="pytest/1.0", stated_confidence=0.9)


API_KEYS = {"test-key-id": "test-secret", "second-tenant": "other-secret"}


@pytest.fixture()
def canonical_app(canonical_db):
    # TestClient dispatches handlers on a worker thread, so the served store
    # needs its own cross-thread connection.
    store = SqliteCanonicalStore(canonical_db, check_same_thread=False)
    yield create_app(store, dict(API_KEYS))
    store.close()


@pytest.fixture()
def service_client(canonical_app):
    with TestClient(canonical_app) as client:
        yield client
