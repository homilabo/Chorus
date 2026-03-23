"""Agent plugin system — load agent definitions from markdown files."""

import logging
import re
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import yaml

from chorus.models import AgentDefinition
from chorus.providers.base import get_available_providers

if TYPE_CHECKING:
    from chorus.conductor import Conductor

logger = logging.getLogger(__name__)

GLOBAL_AGENTS_DIR = Path.home() / ".chorus" / "agents"
LOCAL_AGENTS_DIR = "chorus-agents"  # relative to cwd


# ─── Parsing ───

def parse_agent_file(path: Path) -> Optional[AgentDefinition]:
    """Parse a markdown agent file with YAML frontmatter."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read agent file %s: %s", path, e)
        return None

    # Split frontmatter: ---\n...\n---\n...
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
    if not match:
        logger.warning("No valid frontmatter in %s", path)
        return None

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("Invalid YAML in %s: %s", path, e)
        return None

    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        logger.warning("Agent %s missing required 'name' or 'description'", path)
        return None

    return AgentDefinition(
        name=name,
        description=description,
        mode=meta.get("mode", "parallel"),
        providers=meta.get("providers", "all"),
        rounds=meta.get("rounds", 2),
        prompt_body=match.group(2).strip(),
        source_path=str(path),
    )


# ─── Loading ───

def load_agents(cwd: str = ".") -> dict[str, AgentDefinition]:
    """Load agents from global and project-local directories. Local overrides global."""
    agents: dict[str, AgentDefinition] = {}

    # Global agents
    if GLOBAL_AGENTS_DIR.exists():
        for path in sorted(GLOBAL_AGENTS_DIR.glob("*.md")):
            if path.name == "conductor.md":
                continue
            agent = parse_agent_file(path)
            if agent:
                agents[agent.name] = agent

    # Project-local agents (override global)
    local_dir = Path(cwd) / LOCAL_AGENTS_DIR if cwd else None
    if local_dir and local_dir.exists():
        for path in sorted(local_dir.glob("*.md")):
            if path.name == "conductor.md":
                continue
            agent = parse_agent_file(path)
            if agent:
                agents[agent.name] = agent

    return agents


def load_conductor_override(cwd: str = ".") -> Optional[AgentDefinition]:
    """Load conductor override from project-local or global directory."""
    # Project-local first
    if cwd:
        local_path = Path(cwd) / LOCAL_AGENTS_DIR / "conductor.md"
        if local_path.exists():
            return parse_agent_file(local_path)

    # Global fallback
    global_path = Path.home() / ".chorus" / "conductor.md"
    if global_path.exists():
        return parse_agent_file(global_path)

    return None


# ─── Default Agent Pack ───

DEFAULT_AGENTS = {
    "code-reviewer.md": """---
name: code-reviewer
description: Finds bugs, security issues, and code quality problems in the codebase
mode: parallel
providers: all
---

You are a senior code reviewer. Analyze the codebase in the working directory thoroughly.

Look for:
- Bugs and potential crashes (with file:line references)
- Security vulnerabilities
- Performance issues
- Logic errors and edge cases

Categorize findings by severity:
- **Critical**: Will cause crashes, data loss, or security breaches
- **Medium**: Incorrect behavior, potential issues under certain conditions
- **Low**: Code style, minor improvements, documentation gaps

For each finding, provide the exact file path and line number.
""",

    "debater.md": """---
name: debater
description: Runs a multi-round debate between models on any topic
mode: debate
providers: all
rounds: 2
---

You are participating in a structured multi-model debate.

Rules:
- Present your perspective clearly with specific arguments
- Support claims with evidence, examples, or logical reasoning
- When responding to other models, acknowledge valid points
- Be specific about where and why you disagree
- Aim for constructive discourse, not just contradiction
""",

    "researcher.md": """---
name: researcher
description: Deep research on any topic with multiple perspectives and sources
mode: parallel
providers: all
---

You are a research analyst. Investigate the given topic thoroughly.

Provide:
- Current state and recent developments (2025-2026)
- Key players, tools, or frameworks
- Pros and cons of different approaches
- Your analysis and recommendations
- Sources and references where possible

Be specific and factual. Distinguish between established facts and your analysis.
""",

    "writer.md": """---
name: writer
description: Collaborative content creation where each model builds on the previous
mode: sequential
providers: all
---

You are a collaborative writer. You may receive content from a previous model.

If you're the first writer:
- Create a strong initial draft based on the user's request
- Focus on structure, clarity, and completeness

If you're building on a previous draft:
- Improve the content without losing the original structure
- Fix errors, strengthen weak sections, add missing points
- Clearly note what you changed and why
""",
}


def ensure_default_agents():
    """Create default agent files if the agents directory doesn't exist."""
    if GLOBAL_AGENTS_DIR.exists():
        return  # Don't overwrite existing agents

    GLOBAL_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in DEFAULT_AGENTS.items():
        path = GLOBAL_AGENTS_DIR / filename
        path.write_text(content, encoding="utf-8")
        logger.info("Created default agent: %s", path)


# ─── Agent Executor ───

class AgentExecutor:
    """Executes agent definitions using the conductor's tool methods."""

    def __init__(self, conductor: "Conductor"):
        self.conductor = conductor

    def _resolve_providers(self, agent: AgentDefinition) -> tuple[list[str], list[str]]:
        """Returns (include_list, exclude_list) based on agent's provider config."""
        available = list(get_available_providers().keys())

        if agent.providers == "all":
            return available, []
        if isinstance(agent.providers, list):
            return [p for p in agent.providers if p in available], []
        if isinstance(agent.providers, dict) and "exclude" in agent.providers:
            excluded = agent.providers["exclude"]
            return [p for p in available if p not in excluded], excluded
        return available, []

    def execute(self, agent: AgentDefinition, context: str = "") -> str:
        """Execute an agent based on its mode."""
        prompt = agent.prompt_body
        if context:
            prompt += f"\n\nUser context: {context}"

        providers, exclude = self._resolve_providers(agent)
        if not providers:
            return "No providers available for this agent."

        if agent.mode == "parallel":
            return self.conductor._tool_ask_all(prompt, exclude=exclude)
        elif agent.mode == "debate":
            return self._run_debate(prompt, providers, exclude, agent.rounds)
        elif agent.mode == "sequential":
            return self._run_sequential(prompt, providers)
        elif agent.mode == "cross-review":
            return self._run_cross_review(prompt, providers)
        elif agent.mode == "single":
            return self.conductor._tool_ask_one(providers[0], prompt)
        else:
            return self.conductor._tool_ask_all(prompt, exclude=exclude)

    def _run_debate(self, prompt: str, providers: list[str], exclude: list[str], rounds: int) -> str:
        """Multi-round debate: broadcast, then each round models respond to others."""
        all_results = []

        # Round 0: Initial responses
        initial = self.conductor._tool_ask_all(prompt, exclude=exclude)
        all_results.append(f"**Round 1:**\n{initial}")

        # Subsequent rounds
        for round_num in range(1, rounds):
            debate_prompt = f"""This is round {round_num + 1} of a multi-model debate on the topic below.

Original topic: {prompt}

Previous responses from all models:
{initial}

Now respond again. Consider what other models said. Where do you agree? Disagree? Have you changed your mind? Be specific and constructive."""

            round_result = self.conductor._tool_ask_all(debate_prompt, exclude=exclude)
            all_results.append(f"**Round {round_num + 1}:**\n{round_result}")
            initial = round_result  # Use latest round for next iteration

        return "\n\n---\n\n".join(all_results)

    def _run_sequential(self, prompt: str, providers: list[str]) -> str:
        """Sequential: each model sees previous models' responses."""
        results = []
        accumulated = ""

        for provider_name in providers:
            if accumulated:
                seq_prompt = f"""{prompt}

Previous models' responses:
{accumulated}

Build on, improve, or challenge the previous responses."""
            else:
                seq_prompt = prompt

            result = self.conductor._tool_ask_one(provider_name, seq_prompt)
            display = self.conductor._get_display_name(provider_name)
            results.append(f"**{display}:**\n{result}")
            accumulated += f"\n\n{display}: {result}"

        return "\n\n---\n\n".join(results)

    def _run_cross_review(self, prompt: str, providers: list[str]) -> str:
        """Cross-review: first model generates, second reviews."""
        if len(providers) < 2:
            return self.conductor._tool_ask_one(providers[0], prompt)

        # First model generates
        generator = providers[0]
        reviewer = providers[1]

        gen_result = self.conductor._tool_ask_one(generator, prompt)

        # Second model reviews
        review_result = self.conductor._tool_cross_send(generator, reviewer,
            f"Review the above response critically. What's good? What's wrong or missing? Suggest improvements.")

        gen_display = self.conductor._get_display_name(generator)
        rev_display = self.conductor._get_display_name(reviewer)

        return f"**{gen_display} (Generator):**\n{gen_result}\n\n---\n\n**{rev_display} (Reviewer):**\n{review_result}"
