#!/bin/bash
# Register Chorus MCP servers globally with Claude Code
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python"

echo "Registering Chorus MCP servers (global scope)..."

claude mcp add --scope user chorus-gemini --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/gemini_server.py"
claude mcp add --scope user chorus-copilot --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/copilot_server.py"
claude mcp add --scope user chorus-codex --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/codex_server.py"
claude mcp add --scope user chorus-claude --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/claude_server.py"
claude mcp add --scope user chorus-orchestra --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/orchestra_server.py"
claude mcp add --scope user chorus-memory --transport stdio -- "$PYTHON" "$DIR/chorus/mcp_servers/memory_server.py"

echo ""
echo "Done! Start Claude Code with 'claude' in any directory."
echo "Available tools: ask_gemini, ask_copilot, ask_codex, ask_all, debate, search_memory"
