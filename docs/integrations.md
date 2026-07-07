# Integrating Mnemosyne

Mnemosyne's only surface is MCP over stdio, so any MCP client shares the same
memory fabric. Each assistant is identified from its MCP handshake (`clientInfo`)
and every fact it writes carries that identity as provenance — continuity across
assistants is the point, and `tests/test_continuity_e2e.py` proves it.

Replace `/path/to/mnemosyne` with your checkout path throughout.

## Claude Code

```bash
claude mcp add mnemosyne -- uv run --directory /path/to/mnemosyne mnemosyne-server
```

Or in `.mcp.json` (project scope):

```json
{
  "mcpServers": {
    "mnemosyne": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mnemosyne", "mnemosyne-server"]
    }
  }
}
```

## Claude Desktop

`claude_desktop_config.json` (Settings → Developer → Edit Config). Claude Desktop
does not inherit your shell PATH, so use the absolute path to `uv`
(`which uv`, typically `~/.local/bin/uv`):

```json
{
  "mcpServers": {
    "mnemosyne": {
      "command": "/Users/you/.local/bin/uv",
      "args": ["run", "--directory", "/path/to/mnemosyne", "mnemosyne-server"]
    }
  }
}
```

## Cursor

`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "mnemosyne": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mnemosyne", "mnemosyne-server"]
    }
  }
}
```

## VS Code (GitHub Copilot agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "mnemosyne": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mnemosyne", "mnemosyne-server"]
    }
  }
}
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `MNEMOSYNE_DATA_DIR` | Where `canonical.db` / `overlay.db` live | `~/.mnemosyne` |
| `MNEMOSYNE_ASSISTANT_ID` | Provenance identity for clients that send no `clientInfo` | `unknown-assistant` |
| `MNEMOSYNE_CANONICAL_URL` | Use a hosted canonical tier instead of the local file | unset (local) |
| `MNEMOSYNE_CANONICAL_API_KEY` | Bearer secret for the hosted canonical tier | unset |
| `MNEMOSYNE_CANONICAL_CACHE_TTL` | Client-side cache TTL (seconds) for remote canonical reads | `300` |
| `MNEMOSYNE_EMBEDDINGS` | Semantic resolution: `openai:<model>` or `voyage:<model>` | unset (fuzzy only) |
| `OPENAI_API_KEY` / `VOYAGE_API_KEY` | Key for the configured embedding provider | — |

Add env vars to any client config with the standard `"env": { ... }` block.

## Hosting the canonical tier

Serve the shared ontology to many users/machines from one place:

```bash
# On the host
uv run mnemosyne-seed                          # build canonical.db once
MNEMOSYNE_API_KEYS="jeremy:mk_change_me,team:mk_other" \
  uv run mnemosyne-canonical --host 0.0.0.0 --port 8321
```

Keys are `key_id:secret` pairs (or a JSON file via `MNEMOSYNE_API_KEYS_FILE`);
every request is attributed to its key (`X-Mnemosyne-Key-Id`). The service is
read-only by construction — private overlays never leave the client machine.

```json
{
  "mcpServers": {
    "mnemosyne": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mnemosyne", "mnemosyne-server"],
      "env": {
        "MNEMOSYNE_CANONICAL_URL": "https://canonical.example.com",
        "MNEMOSYNE_CANONICAL_API_KEY": "mk_change_me"
      }
    }
  }
}
```

Smoke-test the service directly:

```bash
curl http://127.0.0.1:8321/v1/health
curl -H "Authorization: Bearer mk_change_me" \
  "http://127.0.0.1:8321/v1/entities/by-name?name=databricks"
```

A reference deployment (marketing site at `/`, canonical API at `/v1/*`) runs at
https://mnemosyne-production-ed7b.up.railway.app — `GET /v1/health` is public;
data endpoints require a key.
