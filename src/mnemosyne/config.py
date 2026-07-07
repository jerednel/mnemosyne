"""Runtime configuration: data directory and assistant identity resolution."""

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".mnemosyne"
CANONICAL_DB_NAME = "canonical.db"
OVERLAY_DB_NAME = "overlay.db"


def data_dir() -> Path:
    """Resolve the data directory, creating it if needed."""
    raw = os.environ.get("MNEMOSYNE_DATA_DIR")
    path = Path(raw).expanduser() if raw else DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def canonical_db_path() -> Path:
    return data_dir() / CANONICAL_DB_NAME


def overlay_db_path() -> Path:
    return data_dir() / OVERLAY_DB_NAME


def fallback_assistant_id() -> str:
    """Assistant identity used when the MCP client does not identify itself."""
    return os.environ.get("MNEMOSYNE_ASSISTANT_ID", "unknown-assistant")
