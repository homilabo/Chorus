#!/bin/bash
# Register Chorus MCP server with all available CLI providers
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python"
SERVER="$DIR/chorus_server.py"

echo "Chorus MCP Setup"
echo "================"

# Check Python venv
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Python venv not found. Run first:"
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install pyyaml mcp"
    exit 1
fi

REGISTERED=0

# ─── Claude Code ───
if command -v claude &>/dev/null; then
    echo ""
    echo "Claude Code:"
    claude mcp remove chorus 2>/dev/null
    claude mcp add --scope user chorus --transport stdio -- "$PYTHON" "$SERVER"
    echo "  ✓ registered"
    REGISTERED=$((REGISTERED + 1))
else
    echo ""
    echo "Claude Code: not installed, skipping"
fi

# ─── Gemini CLI ───
if command -v gemini &>/dev/null; then
    echo ""
    echo "Gemini CLI:"
    GEMINI_SETTINGS="$HOME/.gemini/settings.json"
    mkdir -p "$HOME/.gemini"
    MCP_BLOCK="{\"chorus\":{\"command\":\"$PYTHON\",\"args\":[\"$SERVER\"]}}"
    if [ -f "$GEMINI_SETTINGS" ]; then
        "$PYTHON" -c "
import json
with open('$GEMINI_SETTINGS') as f:
    settings = json.load(f)
settings.setdefault('mcpServers', {})
settings['mcpServers']['chorus'] = {'command': '$PYTHON', 'args': ['$SERVER']}
with open('$GEMINI_SETTINGS', 'w') as f:
    json.dump(settings, f, indent=2)
"
    else
        echo "{\"mcpServers\": $MCP_BLOCK}" | "$PYTHON" -m json.tool > "$GEMINI_SETTINGS"
    fi
    echo "  ✓ registered"
    REGISTERED=$((REGISTERED + 1))
else
    echo ""
    echo "Gemini CLI: not installed, skipping"
fi

# ─── Codex CLI ───
if command -v codex &>/dev/null; then
    echo ""
    echo "Codex CLI:"
    codex mcp remove chorus 2>/dev/null
    codex mcp add chorus -- "$PYTHON" "$SERVER"
    echo "  ✓ registered"
    REGISTERED=$((REGISTERED + 1))
else
    echo ""
    echo "Codex CLI: not installed, skipping"
fi

# ─── Copilot CLI ───
if command -v copilot &>/dev/null; then
    echo ""
    echo "Copilot CLI:"
    COPILOT_MCP="$HOME/.copilot/mcp-config.json"
    mkdir -p "$HOME/.copilot"
    if [ -f "$COPILOT_MCP" ]; then
        "$PYTHON" -c "
import json
with open('$COPILOT_MCP') as f:
    config = json.load(f)
config.setdefault('servers', {})
config['servers']['chorus'] = {'type': 'stdio', 'command': '$PYTHON', 'args': ['$SERVER']}
with open('$COPILOT_MCP', 'w') as f:
    json.dump(config, f, indent=2)
"
    else
        echo "{\"servers\":{\"chorus\":{\"type\":\"stdio\",\"command\":\"$PYTHON\",\"args\":[\"$SERVER\"]}}}" | "$PYTHON" -m json.tool > "$COPILOT_MCP"
    fi
    echo "  ✓ registered"
    REGISTERED=$((REGISTERED + 1))
else
    echo ""
    echo "Copilot CLI: not installed, skipping"
fi

# ─── Summary ───
echo ""
echo "================"
if [ $REGISTERED -eq 0 ]; then
    echo "ERROR: No CLI providers found. Install at least one:"
    echo "  Claude:  https://docs.anthropic.com/en/docs/claude-code"
    echo "  Gemini:  npm install -g @anthropic-ai/gemini-cli"
    echo "  Codex:   npm install -g @openai/codex"
    echo "  Copilot: npm install -g @githubnext/github-copilot-cli"
    exit 1
fi
echo "Done! $REGISTERED provider(s) registered."
echo "Open any registered CLI and start using Chorus tools."
