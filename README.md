# Chorus

**Your AI models, working together.**

Chorus connects your CLI subscriptions (Claude Code, Gemini CLI, Copilot CLI, Codex CLI) into a unified multi-model system via [MCP](https://modelcontextprotocol.io). No API keys needed — just your existing CLI subscriptions.

## How It Works

Chorus runs as a single MCP server. Whichever CLI you open becomes the **conductor** — the others become tools it can call:

```
You <> Claude Code (conductor) -> ask(gemini), ask(codex), ask(copilot)
You <> Gemini CLI  (conductor) -> ask(claude), ask(codex), ask(copilot)
You <> Copilot CLI (conductor) -> ask(claude), ask(gemini), ask(codex)
You <> Codex CLI   (conductor) -> ask(claude), ask(gemini), ask(copilot)
```

## Tools

| Tool | What it does |
|------|-------------|
| `ask` | Ask a specific model by provider or by role |
| `ask_all` | Ask all models the same question in parallel |
| `parallel_ask` | Run multiple calls (same or different providers) simultaneously |
| `run_workflow` | Execute multi-step workflows from markdown templates |

The conductor handles everything else — debates, critiques, research synthesis — using these tools with its own intelligence.

## Roles

Instead of picking a provider, pick a **role** and Chorus routes to the best model:

| Role | Default Provider | Best for |
|------|-----------------|----------|
| `researcher` | Gemini | Deep internet research, large context |
| `coder` | Codex | Code generation, refactoring |
| `reasoner` | Claude | Complex reasoning, architecture |
| `reviewer` | Copilot | Quick code review, best practices |

Roles are configurable in `~/.chorus/config.yaml`. Each role maintains its own conversation session — you can follow up naturally, like talking to a team member.

## Workflows

Define reusable multi-step workflows as markdown files in `~/chorus/workflows/`:

```markdown
# Code Review

## Step 1: Analyze
id: analyze
role: researcher
goal: Analyze the code. Identify patterns, dependencies, and potential issues.
output: Code structure summary, dependencies, and initial observations.

## Step 2: Review
id: review
role: reviewer
goal: Find bugs, edge cases, and best practice violations. Be specific.
inputs: [analyze]
output: Numbered issue list with severity.

## Step 3: Recommend
id: recommend
role: reasoner
goal: Synthesize findings into prioritized, actionable recommendations.
inputs: [analyze, review]
output: Prioritized action list.
```

The conductor reads the workflow and executes each step by calling the appropriate role. Steps can reference previous steps via `inputs`, and `repeat` directives enable review-fix loops.

Run with: `run_workflow(name="code-review")` or `run_workflow(name="list")` to see available workflows.

## Setup

**Requirements:** Python 3.10+ and at least one CLI subscription.

```bash
git clone https://github.com/homilabo/Chorus.git
cd Chorus && ./setup_mcp.sh
```

The setup script:
1. Creates a Python virtual environment
2. Installs dependencies (`pyyaml`, `mcp`)
3. Auto-detects installed CLIs and registers Chorus with each one

Then open any CLI and start using Chorus tools.

## Configuration

Provider models, timeouts, and role mappings are configured in `~/.chorus/config.yaml` (created with defaults on first run):

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

roles:
  researcher:
    provider: gemini
    model: gemini-2.5-pro
    description: Deep internet research, large context analysis
  coder:
    provider: codex
    model: gpt-5.4
    description: Code generation, complex logic, refactoring
  reasoner:
    provider: claude
    model: opus
    description: Complex reasoning, architecture, analysis
  reviewer:
    provider: copilot
    model: gpt-5-mini
    description: Quick code review, best practices
```

## Example Usage

```
You:    Compare Ruby and Python for web development
Claude: [calls ask_all] -> 4 models respond in parallel -> synthesizes results

You:    Research the latest MCP specification changes
Claude: [calls ask(role="researcher")] -> Gemini does deep web research

You:    Now have the reasoner critique that analysis
Claude: [calls ask(role="reasoner", prompt="Gemini said: ... critique this")]

You:    Run a 2-round debate on REST vs GraphQL
Claude: [calls ask_all round 1] -> [calls ask_all round 2 with context] -> synthesizes

You:    Execute the code-review workflow on this project
Claude: [calls run_workflow("code-review")] -> executes 3 steps with different roles
```

## Architecture

```
Chorus/
├── chorus_server.py   # MCP server — tools + workflow engine
├── cli.py             # CLI provider subprocess calls + session tracking
├── config.py          # Provider/role config from ~/.chorus/config.yaml
├── setup_mcp.sh       # Auto-register with installed CLIs
├── pyproject.toml
└── README.md
```

~530 lines of Python. No frameworks, no API keys, no external services.

## How It's Different

- **No API costs** — uses CLI subscriptions you already pay for
- **Any conductor** — Claude, Gemini, Copilot, or Codex can orchestrate
- **Role-based routing** — pick the right model for the task automatically
- **Parallel execution** — `ask_all` runs all models simultaneously
- **Session continuity** — each role remembers its conversation
- **Workflow engine** — reusable multi-step templates in markdown
- **~530 LOC** — easy to understand and modify

## License

MIT
