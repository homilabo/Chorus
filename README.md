# Chorus

**Your AI models, working together.**

Chorus connects your CLI subscriptions (Claude, Gemini, Copilot, Codex) into a unified multi-model system via [MCP](https://modelcontextprotocol.io). No API keys needed — just your existing ~$20/month subscriptions.

## How It Works

Chorus runs as a single MCP server. Whichever CLI you open becomes the conductor — the others become tools it can call:

```
You ↔ Claude Code (conductor) → ask(gemini), ask(codex), ask(copilot)
You ↔ Gemini CLI  (conductor) → ask(claude), ask(codex), ask(copilot)
You ↔ Copilot CLI (conductor) → ask(claude), ask(gemini), ask(codex)
You ↔ Codex CLI   (conductor) → ask(claude), ask(gemini), ask(copilot)
```

## Tools

| Tool | What it does |
|------|-------------|
| `ask` | Ask a specific model (provider: gemini, copilot, codex, claude) |
| `ask_all` | Ask all models the same question in parallel |
| `parallel_ask` | Run multiple calls (same or different providers) simultaneously |

The conductor handles everything else — debates, critiques, research synthesis — using these 3 tools with its own intelligence.

## Setup

**Requirements:** Python 3.10+ and at least one CLI subscription.

```bash
# 1. Clone
git clone https://github.com/willynikes/chorus.git
cd chorus

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml mcp

# 3. Register MCP server (auto-detects installed CLIs)
./setup_mcp.sh

# 4. Open any CLI
claude   # or: gemini, copilot, codex
```

That's it. The conductor will automatically discover Chorus tools.

## Configuration

Models and timeouts are configured in `~/.chorus/config.yaml` (created on first run):

```yaml
providers:
  claude:
    model: sonnet
    timeout: 300
    max_turns: 10
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

## Example Usage

```
You:    Compare Ruby and Python, ask all models
Claude: [calls ask_all] → 4 models respond in parallel → synthesizes results

You:    Ask Gemini to research DMK Karavan
Claude: [calls ask(provider="gemini")] → returns Gemini's research

You:    Now send that to Codex for critique
Claude: [calls ask(provider="codex", prompt="Gemini said: ... critique this")]

You:    Run a 2-round debate on REST vs GraphQL
Claude: [calls ask_all round 1] → [calls ask_all round 2 with previous context] → synthesizes
```

## Architecture

```
chorus/
├── chorus_server.py    # MCP server — 3 tools
├── cli.py              # CLI provider call functions
├── config.py           # Provider config (~/.chorus/config.yaml)
├── setup_mcp.sh        # Auto-register with installed CLIs
└── README.md
```

~350 lines of Python. No frameworks, no API keys.

## How It's Different

- **No API costs** — uses CLI subscriptions you already pay for
- **Any conductor** — Claude, Gemini, Copilot, or Codex can orchestrate
- **Parallel execution** — `ask_all` runs all models simultaneously
- **Session continuity** — models remember context within a session
- **3 files** — no bloat, easy to understand and modify
