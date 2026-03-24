#!/bin/bash
# Register Chorus MCP server with all available CLI providers
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python"
SERVER="$DIR/chorus_server.py"

echo "Chorus MCP Setup"
echo "================"

# Create venv and install dependencies if needed
if [ ! -f "$PYTHON" ]; then
    echo "Setting up Python environment..."
    python3 -m venv "$DIR/.venv"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create venv. Is Python 3.10+ installed?"
        exit 1
    fi
fi

echo "Installing dependencies..."
"$PYTHON" -m pip install --quiet -e "$DIR" 2>&1 | tail -1

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
config.setdefault('mcpServers', {})
config['mcpServers']['chorus'] = {'type': 'stdio', 'command': '$PYTHON', 'args': ['$SERVER']}
with open('$COPILOT_MCP', 'w') as f:
    json.dump(config, f, indent=2)
"
    else
        echo "{\"mcpServers\":{\"chorus\":{\"type\":\"stdio\",\"command\":\"$PYTHON\",\"args\":[\"$SERVER\"]}}}" | "$PYTHON" -m json.tool > "$COPILOT_MCP"
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
