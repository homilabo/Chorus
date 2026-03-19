# Chorus

**Multi-LLM deliberation tool.** Have your AI models discuss, debate, and build on each other's ideas — CLI-first, ~$60/month flat.

## The Problem

You're already using multiple AI models. But making them work together means endless copy-paste between terminals. And API costs can hit $1000+/month for multi-model workflows.

## The Solution

Chorus connects your existing CLI subscriptions (Claude, Gemini, Copilot, Codex) into a single terminal where models can see and respond to each other — with shared memory across sessions.

```
chorus> /all Should I use microservices or monolith for a 3-person startup?

┌─ Claude [sonnet] (4.2s) ───────────────────┐
│ Start with a monolith. With 3 developers... │
└─────────────────────────────────────────────┘
┌─ Gemini [2.5-pro] (3.8s) ──────────────────┐
│ Consider microservices if you expect rapid...│
└─────────────────────────────────────────────┘
┌─ Copilot [gpt-4.1] (5.1s) ─────────────────┐
│ Modular monolith gives you the best of both..│
└─────────────────────────────────────────────┘

chorus> /debate 2
[Round 2 - models respond to each other's arguments...]

chorus> /synth
[Synthesis: All 3 models converge on modular monolith...]
```

## Install

```bash
git clone https://github.com/YOUR_USER/chorus.git
cd chorus
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Requirements

At least one CLI tool installed:
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini`)
- [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli) (`copilot`)
- [OpenAI Codex CLI](https://github.com/openai/codex) (`codex`)

Each is ~$20/month subscription. Use 3 = $60/month flat for unlimited multi-model deliberation.

## Usage

```bash
chorus                    # Start interactive REPL
chorus --cwd ./my-project # Set working directory for code tasks
```

### Commands

| Command | What it does |
|---------|-------------|
| `/all <prompt>` | Ask all models the same question |
| `/ask <model> <prompt>` | Ask a specific model |
| `@claude <prompt>` | Shortcut for `/ask claude` |
| `/cross <from> <to>` | Send one model's response to another |
| `/debate [rounds] <topic>` | Multi-round debate between all models |
| `/synth` | Synthesize all perspectives into one answer |
| `/memory <query>` | Search past conversations |
| `/sessions` | List past sessions |
| `/models` | Show available models |

### Example: Cross-Model Critique

```
chorus> @claude Explain Kubernetes pod scheduling

chorus> /cross claude gemini
[Gemini reviews Claude's explanation and adds corrections...]

chorus> /cross gemini codex
[Codex reviews Gemini's critique and adds its perspective...]
```

### Example: Debate

```
chorus> /debate 3 REST vs GraphQL for a mobile app backend?
[Round 1: All models share initial positions]
[Round 2: Models respond to each other's arguments]
[Round 3: Models refine positions, find consensus]

chorus> /synth
[One model synthesizes the entire debate]
```

## How It Works

1. **CLI-First**: Calls your installed CLI tools via subprocess — no API keys needed
2. **Shared Context**: All models see what others said via conversation memory
3. **Parallel Execution**: `/all` and `/debate` run models in parallel
4. **Persistent Memory**: SQLite + FTS5 stores all conversations, searchable across sessions
5. **Cross-Send**: The killer feature — send any model's response to any other for critique

## Architecture

```
User Input → REPL → Orchestrator → CLI Providers (parallel subprocess calls)
                         ↕
                   Shared Memory (SQLite + FTS5)
```

~2000 lines of Python. No frameworks. No complexity.

## Config

Config lives at `~/.chorus/config.yaml`. Created automatically on first run.

```yaml
providers:
  claude:
    enabled: true
    model: sonnet
    timeout: 300
  gemini:
    enabled: true
    model: gemini-2.5-pro
  copilot:
    enabled: true
    model: gpt-4.1
  codex:
    enabled: true
    model: gpt-5.4
```

## Why Chorus?

| | API-based tools | Chorus |
|---|---|---|
| **Cost** | $500-1000+/month | ~$60/month flat |
| **Cross-model talk** | Rare | Built-in |
| **Shared memory** | Usually no | Yes, SQLite + FTS5 |
| **Setup** | API keys, billing | Just CLI subscriptions |
| **Privacy** | Data sent to APIs | Same as your CLI tools |

## License

MIT
