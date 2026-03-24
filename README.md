# Chorus

**Your AI models, working together.**

Chorus connects your CLI subscriptions (Claude, Gemini, Copilot, Codex) into a unified multi-model system via MCP (Model Context Protocol). No API keys needed — just your existing $20/month subscriptions.

## How It Works

Chorus runs as a single MCP server that any compatible AI CLI can use as a conductor:

```
You ↔ Claude Code (conductor) → ask_gemini(), ask_codex(), ask_copilot()
You ↔ Gemini CLI (conductor) → ask_claude(), ask_codex(), ask_copilot()
```

Whichever CLI you open becomes the conductor. The others become tools it can call.

## Available Tools

| Tool | Description |
|------|-------------|
| `ask_gemini` | Ask Google Gemini (internet research, analysis) |
| `ask_copilot` | Ask GitHub Copilot (code tasks) |
| `ask_codex` | Ask OpenAI Codex (code generation, analysis) |
| `ask_claude` | Ask Claude (reasoning, coding) |
| `ask_all` | Ask all models in parallel |
| `debate` | Multi-round debate between models |
| `cross_send` | Send one model's response to another for critique |
| `search_memory` | Search past conversations (FTS5) |
| `save_to_memory` | Save content for future retrieval |
| `save_session_summary` | Save session summary with key topics |

## Setup

```bash
# 1. Install dependencies
cd Chorus && python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Register MCP server with all CLI providers
./setup_mcp.sh

# 3. Open any CLI and start talking
claude   # or: gemini, copilot, codex
```

## Configuration

Edit `~/.chorus/config.yaml` to set models and timeouts:

```yaml
providers:
  claude:
    model: sonnet
    timeout: 300
  gemini:
    model: gemini-2.5-pro
    timeout: 300
  copilot:
    model: gpt-5-mini
    timeout: 300
  codex:
    model: gpt-5.4
    timeout: 300
```

## Project Integration

Copy `CLAUDE.md` to any project directory to give the conductor behavior guidelines (when to delegate, how to synthesize results).

## Architecture

```
chorus/
├── cli.py                 # CLI provider call functions
├── config.py              # Provider configuration
├── memory.py              # SQLite + FTS5 persistent memory
├── models.py              # Data models
└── mcp_servers/
    └── chorus_server.py   # Single MCP server, 12 tools
```

~700 lines of Python. No frameworks, no API keys.
