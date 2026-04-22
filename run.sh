#!/usr/bin/env bash
# Launcher for Buonaiuto Doc4LLM (macOS / Linux)
# Prompts for which mode to start, then runs it.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Error: no Python interpreter found. Set PYTHON_BIN env var." >&2
    exit 1
fi

export PYTHONPATH="$BASE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

cat <<'EOF'
Buonaiuto Doc4LLM — choose a mode:

  1) MCP stdio server only             (Claude Code / Cursor / Windsurf — subprocess)
  2) MCP stdio server + dashboard      (stdio + website at http://127.0.0.1:8420)
  3) Dashboard only                    (website at http://127.0.0.1:8420)
  4) Watch docs_center/ for changes    (auto re-scan)
  5) MCP HTTP server only              (Claude Desktop / claude.ai — http://127.0.0.1:8421/mcp)
  6) MCP stdio + HTTP + dashboard      (all three in one process)

EOF

read -rp "Enter choice [1-6]: " choice

case "$choice" in
    1)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve
        ;;
    2)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve --dashboard
        ;;
    3)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" dashboard
        ;;
    4)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" watch
        ;;
    5)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve-http
        ;;
    6)
        exec "$PYTHON_BIN" -m buonaiuto_doc4llm --base-dir "$BASE_DIR" serve \
            --http --http-port 8421 \
            --dashboard --dashboard-port 8420
        ;;
    *)
        echo "Invalid choice: $choice" >&2
        exit 1
        ;;
esac
