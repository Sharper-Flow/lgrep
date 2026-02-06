# lgrep: Local Semantic Code Search for OpenCode

`lgrep` is a high-performance semantic code search MCP server designed for OpenCode. It combines **Voyage Code 3** embeddings (92% retrieval quality) with **local LanceDB** vector storage for fast, conceptually accurate results that respect your privacy.

## Key Features

- **Semantic Intelligence**: Finds code by meaning, not just keywords (e.g., search for "JWT verification" to find `token.validate()`).
- **Hybrid Search**: Combines vector similarity with BM25 keyword matching for optimal precision and recall.
- **Local Storage**: Vectors and code snippets stay on your machine; only anonymized queries reach the Voyage API.
- **Agent Optimized**: Built specifically for AI agents with 3+ concurrent query support and sub-200ms latency.
- **AST-Aware Chunking**: Uses `Chonkie` and `tree-sitter` for intelligent code segmenting that preserves structural context.

## Installation

```bash
# Clone the repository
git clone https://github.com/anomalyco/lgrep.git
cd lgrep

# Install dependencies and the package
pip install .
```

After installation, the `lgrep` command is available to start the server.

## Configuration

`lgrep` requires a Voyage AI API key. You can get one at [dash.voyageai.com](https://dash.voyageai.com/).

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VOYAGE_API_KEY` | **Required**. Your Voyage AI API key. | None |
| `LGREP_LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR). | `INFO` |
| `LGREP_CACHE_DIR` | Directory to store LanceDB vector databases. | `~/.cache/lgrep` |

### Ignoring Files

`lgrep` respects `.gitignore` patterns. You can also create a `.lgrepignore` file in your project root to exclude additional files or directories from indexing.

## Setup with OpenCode

Add the following to your `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "lgrep": {
      "type": "local",
      "command": ["lgrep"],
      "env": {
        "VOYAGE_API_KEY": "your-voyage-api-key-here"
      },
      "enabled": true
    }
  }
}
```

## Available Tools

- `lgrep_index(path: str)`: Build a full index for the specified project directory. Automatically skips unchanged files using SHA-256 hashes.
- `lgrep_search(query: str, limit: int = 10, hybrid: bool = true)`: Search code semantically using natural language.
- `lgrep_status()`: Get current indexing statistics (file count, chunk count, project path).
- `lgrep_watch_start(path: str)`: Enable background monitoring for incremental re-indexing on file saves.
- `lgrep_watch_stop()`: Disable the background file watcher.

## Resource Usage

- **RAM**: ~300MB idle, ~500MB during indexing.
- **Latency**: ~90ms for embedding + ~20ms for local search.
- **Cost**: ~$3/month for active usage (Voyage Code 3 pricing: $0.18/1M tokens).

## Troubleshooting

- **API Key Missing**: Ensure `VOYAGE_API_KEY` is set in the `env` section of your OpenCode config.
- **Dependency Issues**: `lgrep` depends on native extensions for LanceDB and tree-sitter. Ensure you have a C compiler if prebuilt wheels aren't available for your platform.
- **Index Not Updating**: Try calling `lgrep_index` to force a refresh, or check the logs for file watcher errors.

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## License

MIT
