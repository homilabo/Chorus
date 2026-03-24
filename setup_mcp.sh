#!/bin/bash
# Register Chorus MCP server with all supported CLI providers
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python"
SERVER="$DIR/chorus/mcp_servers/chorus_server.py"

# ─── Claude Code ───
echo "=== Claude Code ==="
claude mcp remove chorus 2>/dev/null
claude mcp add --scope user chorus --transport stdio -- "$PYTHON" "$SERVER"
echo "  ✓ chorus registered"

# ─── Gemini CLI ───
echo ""
echo "=== Gemini CLI ==="
GEMINI_SETTINGS="$HOME/.gemini/settings.json"
mkdir -p "$HOME/.gemini"

MCP_BLOCK="{\"chorus\":{\"command\":\"$PYTHON\",\"args\":[\"$SERVER\"]}}"

if [ -f "$GEMINI_SETTINGS" ]; then
    "$PYTHON" -c "
import json
with open('$GEMINI_SETTINGS') as f:
    settings = json.load(f)
settings['mcpServers'] = json.loads('$MCP_BLOCK')
with open('$GEMINI_SETTINGS', 'w') as f:
    json.dump(settings, f, indent=2)
print('  ✓ Updated existing settings.json')
"
else
    echo "{\"mcpServers\": $MCP_BLOCK}" | "$PYTHON" -m json.tool > "$GEMINI_SETTINGS"
    echo "  ✓ Created settings.json"
fi

# ─── Codex CLI ───
echo ""
echo "=== Codex CLI ==="
codex mcp remove chorus 2>/dev/null
codex mcp add chorus -- "$PYTHON" "$SERVER"
echo "  ✓ chorus registered"

# ─── Copilot CLI ───
echo ""
echo "=== Copilot CLI ==="
COPILOT_MCP="$HOME/.copilot/mcp-config.json"
mkdir -p "$HOME/.copilot"

COPILOT_BLOCK="{\"servers\":{\"chorus\":{\"type\":\"stdio\",\"command\":\"$PYTHON\",\"args\":[\"$SERVER\"]}}}"

if [ -f "$COPILOT_MCP" ]; then
    "$PYTHON" -c "
import json
with open('$COPILOT_MCP') as f:
    config = json.load(f)
config.setdefault('servers', {})
config['servers']['chorus'] = {'type': 'stdio', 'command': '$PYTHON', 'args': ['$SERVER']}
with open('$COPILOT_MCP', 'w') as f:
    json.dump(config, f, indent=2)
print('  ✓ Updated existing mcp-config.json')
"
else
    echo "$COPILOT_BLOCK" | "$PYTHON" -m json.tool > "$COPILOT_MCP"
    echo "  ✓ Created mcp-config.json"
fi

echo ""
echo "Done! One server registered with all 4 CLI providers."
echo ""
echo "Tools: ask_gemini, ask_copilot, ask_codex, ask_claude"
echo "       ask_all, debate, cross_send"
echo "       search_memory, save_to_memory, save_session_summary"
echo "       get_recent_sessions, search_summaries"
