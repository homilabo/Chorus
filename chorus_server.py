"""Chorus MCP server — multi-model orchestration via CLI subscriptions."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

WORKFLOW_DIR = Path.home() / "chorus" / "workflows"


def _build_instructions() -> str:
    """Build MCP instructions with roles from config."""
    from config import get_roles
    roles = get_roles()

    lines = [
        "You have access to multiple AI models via Chorus tools.",
        "",
        "Model tools:",
        "- ask: Ask a specific model by provider name OR by role",
        "- ask_all: Ask all models in parallel — use for comparisons, research, multiple perspectives",
        "- parallel_ask: Run multiple calls (same or different providers) simultaneously",
        "- managed_task: Let an owner split a task, run workers in parallel, then summarize",
    ]

    if roles:
        lines.append("")
        lines.append("Available roles (use with ask tool's role parameter):")
        for name, cfg in roles.items():
            provider = cfg.get("provider", "?")
            model = cfg.get("model", "default")
            desc = cfg.get("description", "")
            lines.append(f"- {name}: {desc} (→ {provider}/{model})")

    lines.extend([
        "",
        "Workflows:",
        "- run_workflow: Execute a predefined multi-step workflow from ~/chorus/workflows/",
        "- Workflows are markdown files with numbered steps, each specifying a role and prompt",
        "- Use run_workflow(name='list') to see available workflows",
        "",
        "Sessions:",
        "- Each role maintains its own conversation session (coder, researcher, etc.)",
        "- Use agent_id for isolated same-provider workers inside one larger task",
        "- You can follow up with the same role and it will remember the conversation",
        "- Responses include [session: ID] — use session parameter to resume a specific conversation",
        "- Example: ask(role='coder', session='abc-123', prompt='continue from where we left off')",
        "- Treat roles like team members — give feedback, ask for corrections, iterate",
        "- Models have full CLI access (files, terminal, web) — don't paste code, give file paths",
        "",
        "Guidelines:",
        "- Match the user's language",
        "- For simple questions, answer directly without calling models",
        "- For comparisons, debates, research — use ask_all for multiple perspectives",
        "- Use roles to pick the right model for the task (e.g. role='researcher' for research)",
        "- For debates: call ask_all for round 1, then ask_all again with previous responses as context",
        "- For critique: use ask to send one model's response to another",
        "- Synthesize results by highlighting agreements, disagreements, and insights",
        "- Keep the user informed: before each step, say what you're doing. After each response, briefly summarize what the model said before moving on.",
        "- Be natural and conversational",
    ])

    return "\n".join(lines)


AVAILABLE_PROVIDERS = ["gemini", "copilot", "codex", "claude"]

mcp = FastMCP("chorus", instructions=_build_instructions())


def _get_provider_fn(name: str):
    """Get the CLI call function for a provider."""
    from cli import call_gemini, call_copilot, call_codex, call_claude
    return {
        "gemini": call_gemini,
        "copilot": call_copilot,
        "codex": call_codex,
        "claude": call_claude,
    }.get(name)


def _resolve_role(role: str) -> tuple[str, str | None]:
    """Resolve a role name to (provider, model). Returns (provider, model_override)."""
    from config import get_role
    role_cfg = get_role(role)
    if not role_cfg:
        return role, None
    return role_cfg.get("provider", "gemini"), role_cfg.get("model")


def _agent_session_key(provider: str, agent_id: str) -> str:
    """Return an isolated session key for a named agent."""
    return f"agent:{provider}:{agent_id}" if agent_id else ""


def _role_names() -> list[str]:
    from config import get_roles
    return sorted(get_roles())


def _strip_session_footer(text: str) -> str:
    return text.split("\n\n[session:", 1)[0].strip()


def _task_key(prompt: str, agent_prefix: str) -> str:
    if agent_prefix:
        return agent_prefix
    return "managed-" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def _extract_json_object(text: str) -> dict:
    text = _strip_session_footer(text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _valid_provider(provider: str) -> bool:
    return bool(provider and _get_provider_fn(provider))


def _valid_role(role: str) -> bool:
    if not role:
        return False
    provider, _model = _resolve_role(role)
    return bool(_get_provider_fn(provider))


def _worker_tasks(plan_text: str, goal: str, default_worker_role: str, max_workers: int, task_key: str) -> list[dict]:
    data = _extract_json_object(plan_text)
    planned = data.get("tasks", [])
    if not isinstance(planned, list):
        planned = []

    tasks = []
    for idx, item in enumerate(planned[:max_workers], start=1):
        if not isinstance(item, dict):
            continue
        worker_prompt = item.get("prompt") or item.get("goal") or item.get("task")
        if not worker_prompt:
            continue
        task = {
            "agent_id": item.get("agent_id") or f"{task_key}-worker-{idx}",
            "prompt": worker_prompt,
        }
        if _valid_provider(item.get("provider", "")):
            task["provider"] = item["provider"]
        elif _valid_role(item.get("role", "")):
            task["role"] = item["role"]
        else:
            task["role"] = default_worker_role
        tasks.append(task)

    if tasks:
        return tasks
    return [{
        "role": default_worker_role,
        "agent_id": f"{task_key}-worker-1",
        "prompt": goal,
    }]


# ─── Tools ───

@mcp.tool()
async def ask(prompt: str, provider: str = "", role: str = "", session: str = "", cwd: str = ".", agent_id: str = "") -> str:
    """Ask a specific AI model a question, either by provider name or by role.

    By role (recommended): role="researcher", role="coder", role="reasoner", role="reviewer"
    By provider: provider="gemini", provider="copilot", provider="codex", provider="claude"

    Roles map to the best provider+model for that task (configured in ~/.chorus/config.yaml).
    agent_id creates an isolated session for this logical agent on the selected provider.

    Args:
        prompt: The question or instruction
        role: Task role — routes to the best provider+model automatically
        provider: Direct provider name (use role instead when possible)
        session: Session ID to resume a previous conversation (from a prior ask response)
        cwd: Working directory for file operations
        agent_id: Stable logical agent name for an isolated provider session
    """
    model_override = None
    if role:
        provider, model_override = _resolve_role(role)
    elif not provider:
        provider = "gemini"

    fn = _get_provider_fn(provider)
    if not fn:
        return f"Unknown provider: {provider}. Available: {', '.join(AVAILABLE_PROVIDERS)}"

    # Use agent_id for isolated workers; otherwise preserve role/provider sessions.
    session_key = _agent_session_key(provider, agent_id) or (role if role else provider)

    # If explicit session ID provided, inject it so the provider resumes that conversation
    if session:
        from cli import set_session
        set_session(session_key, session)

    loop = asyncio.get_event_loop()
    if model_override:
        result = await loop.run_in_executor(None, lambda: fn(prompt, model=model_override, cwd=cwd, session_key=session_key))
    else:
        result = await loop.run_in_executor(None, lambda: fn(prompt, cwd=cwd, session_key=session_key))

    if result.error:
        return f"[{provider.upper()} ERROR] {result.error}"

    # Append session ID so the caller can resume this conversation later
    response = result.text
    if result.session_id:
        response += f"\n\n[session: {result.session_id}]"
    return response


@mcp.tool()
async def ask_all(prompt: str, exclude: list[str] = None, cwd: str = ".") -> str:
    """Ask ALL available models the same question in parallel and return all responses.

    Use this for comparisons, research, or getting multiple perspectives.
    All models run simultaneously — total time equals the slowest model.

    Args:
        prompt: The question to ask all models
        exclude: Models to skip (e.g. ["copilot", "claude"])
        cwd: Working directory for file operations
    """
    exclude = exclude or []
    active = {name: _get_provider_fn(name) for name in AVAILABLE_PROVIDERS if name not in exclude}

    if not active:
        return "No providers available."

    loop = asyncio.get_event_loop()

    async def _call(name, fn):
        result = await loop.run_in_executor(None, lambda: fn(prompt, cwd=cwd))
        return name, result

    tasks = [_call(name, fn) for name, fn in active.items()]
    results = await asyncio.gather(*tasks)

    parts = []
    for name, result in results:
        if result.error:
            parts.append(f"**{name.upper()}**: ERROR — {result.error}")
        else:
            parts.append(f"**{name.upper()}** ({result.duration_ms}ms):\n{result.text}")

    return "\n\n---\n\n".join(parts)


@mcp.tool()
async def parallel_ask(tasks: list[dict], cwd: str = ".") -> str:
    """Run multiple ask calls in parallel. Each task specifies a provider/role and prompt.

    Use this when you need to call the same or different providers multiple times simultaneously.
    All tasks run at the same time — total time equals the slowest task.

    Args:
        tasks: List of objects with "prompt" and either "provider" or "role" keys.
               Optional "agent_id" isolates same-provider sessions.
               Example: [{"role": "coder", "agent_id": "worker-a", "prompt": "question 1"}]
        cwd: Working directory for file operations
    """
    if not tasks:
        return "No tasks provided."

    loop = asyncio.get_event_loop()

    async def _call(idx, task):
        prompt = task.get("prompt", "")
        role = task.get("role", "")
        provider = task.get("provider", "")
        agent_id = task.get("agent_id", "")
        model_override = None

        if role:
            provider, model_override = _resolve_role(role)
        elif not provider:
            provider = "gemini"

        fn = _get_provider_fn(provider)
        if not fn:
            return idx, provider, None, f"Unknown provider: {provider}"

        kwargs = {"cwd": cwd}
        if model_override:
            kwargs["model"] = model_override
        session_key = _agent_session_key(provider, agent_id)
        if session_key:
            kwargs["session_key"] = session_key
        result = await loop.run_in_executor(None, lambda: fn(prompt, **kwargs))
        return idx, provider, result, None

    jobs = [_call(i, t) for i, t in enumerate(tasks)]
    results = await asyncio.gather(*jobs)

    parts = []
    for idx, provider, result, error in sorted(results, key=lambda x: x[0]):
        label = f"**[{idx+1}] {provider.upper()}**"
        if error:
            parts.append(f"{label}: ERROR — {error}")
        elif result.error:
            parts.append(f"{label}: ERROR — {result.error}")
        else:
            parts.append(f"{label} ({result.duration_ms}ms):\n{result.text}")

    return "\n\n---\n\n".join(parts)


@mcp.tool()
async def managed_task(
    prompt: str,
    owner_role: str = "reasoner",
    max_workers: int = 3,
    cwd: str = ".",
    agent_prefix: str = "",
    default_worker_role: str = "coder",
) -> str:
    """Run a small owner -> parallel workers -> owner summary loop.

    Args:
        prompt: The task to manage
        owner_role: Role that plans and summarizes the work
        max_workers: Maximum number of owner-planned worker tasks, clamped to 1..5
        cwd: Working directory for file operations
        agent_prefix: Optional stable prefix for owner/worker session IDs
        default_worker_role: Worker role to use only when the owner omits role/provider
    """
    max_workers = max(1, min(max_workers, 5))
    key = _task_key(prompt, agent_prefix)
    valid_roles = ", ".join(_role_names()) or "none"
    valid_providers = ", ".join(AVAILABLE_PROVIDERS)

    plan_prompt = (
        "You are the owner for this task. Decide whether workers are needed and, "
        f"if so, split the work into at most {max_workers} independent worker tasks.\n"
        "Do not solve the task yourself in this step. Only create the worker plan.\n"
        "You choose each worker's role or provider and write the worker prompts. "
        "The orchestrator will only execute your JSON plan.\n"
        f"Valid roles: {valid_roles}.\n"
        f"Valid providers: {valid_providers}.\n"
        "Return only JSON in this shape:\n"
        '{"tasks":[{"agent_id":"worker-1","role":"coder","prompt":"specific worker task"}]}\n'
        "Use either role or provider on each task. If you omit both, the default worker role is "
        f"{default_worker_role}.\n\n"
        f"Task:\n{prompt}"
    )
    owner_plan = await ask(plan_prompt, role=owner_role, cwd=cwd, agent_id=f"{key}-owner")
    clean_plan = _strip_session_footer(owner_plan)
    tasks = _worker_tasks(clean_plan, prompt, default_worker_role, max_workers, key)

    worker_results = await parallel_ask(tasks, cwd=cwd)

    final_prompt = (
        "You are the owner. Summarize the worker results for the manager.\n"
        "Be concise, call out failures or follow-up needed, and do not invent completed work.\n\n"
        f"Original task:\n{prompt}\n\n"
        f"Owner plan:\n{clean_plan}\n\n"
        f"Worker results:\n{worker_results}"
    )
    owner_final = await ask(final_prompt, role=owner_role, cwd=cwd, agent_id=f"{key}-owner")

    return (
        "## Owner Plan\n\n"
        f"{clean_plan}\n\n"
        "---\n\n"
        "## Worker Results\n\n"
        f"{worker_results}\n\n"
        "---\n\n"
        "## Owner Final\n\n"
        f"{_strip_session_footer(owner_final)}"
    )


@mcp.tool()
async def run_workflow(name: str) -> str:
    """Run a predefined multi-step workflow from ~/chorus/workflows/.

    Workflows are markdown files with numbered steps. Each step specifies a role
    and a description. The conductor executes steps in order using Chorus tools,
    crafting prompts based on the conversation context.

    Args:
        name: Workflow name (filename without .md). Use "list" to see available workflows.
    """
    if name == "list" or not name:
        if not WORKFLOW_DIR.exists():
            return f"No workflows directory. Create workflows in {WORKFLOW_DIR}/"
        files = sorted(WORKFLOW_DIR.glob("*.md"))
        if not files:
            return f"No workflows found in {WORKFLOW_DIR}/"
        items = []
        for f in files:
            first_line = f.read_text(encoding="utf-8").split("\n", 1)[0].strip("# \n")
            items.append(f"- **{f.stem}**: {first_line}")
        return "Available workflows:\n" + "\n".join(items)

    path = WORKFLOW_DIR / f"{name}.md"
    if not path.exists():
        # Try without .md
        path = WORKFLOW_DIR / name
        if not path.exists():
            files = sorted(WORKFLOW_DIR.glob("*.md")) if WORKFLOW_DIR.exists() else []
            available = ", ".join(f.stem for f in files) if files else "none"
            return f"Workflow '{name}' not found. Available: {available}"

    content = path.read_text(encoding="utf-8")

    return (
        f"## Workflow: {name}\n\n"
        f"{content}\n\n"
        "---\n"
        "INSTRUCTIONS: Execute each step IN ORDER by calling Chorus MCP tools.\n"
        "\n"
        "Reading the workflow:\n"
        "- Each step has: id, role, goal, output, and optionally inputs and cwd.\n"
        "- role: call ask(role=\"...\") with that role. If role is ask_all, call ask_all() instead.\n"
        "- goal: what to tell the role. Craft your prompt based on this.\n"
        "- output: what you expect back. If the response doesn't match, follow up.\n"
        "- inputs: [id1, id2] means include those steps' outputs as context in your prompt.\n"
        "- cwd: pass as cwd parameter for file operations.\n"
        "- repeat: loop between the named steps until the condition is met or max is reached.\n"
        "\n"
        "Rules:\n"
        "- Do NOT skip steps. Do NOT do the work yourself — delegate to the models.\n"
        "- Each model has full CLI access (file read/write, terminal, web). Use cwd parameter for file context.\n"
        "- Do NOT paste file contents into prompts — tell the model the file path, it can read it.\n"
        "- For code tasks, tell the model to WRITE files directly, not just describe what to write.\n"
        "- Each role keeps its own conversation session — you can follow up naturally.\n"
        "- Before each step, tell the user what you're about to do.\n"
        "- After each response, briefly summarize what the model said before moving on.\n"
        "- After all steps, give the user a final summary."
    )


@mcp.tool()
async def recall(
    action: str,
    query: str = "",
    project: str = "",
    session_id: str = "",
    source: str = "",
    limit: int = 10,
) -> str:
    """Search past AI-coding-agent sessions (Claude Code transcripts) for cross-session memory.

    Use this when the user asks about prior work — "what did we do in project X?",
    "where did we leave off?", "find that auth thing we discussed last week". Auto-indexes
    incrementally on every call (read-only over ~/.claude/projects/**.jsonl).

    Actions:
        files  — most recently touched files (Tier 1, ~50 tokens). Filter with `project`.
        list   — most recent sessions with one-line summaries. Filter with `project`/`source`.
        search — full-text search across all turns. Filter with `project`/`source`. Use this for topic recall.
        show   — full transcript for a session_id (use after `list` or `search`).
        health — DB stats / latency probe.

    Args:
        action:     one of files | list | search | show | health
        query:      search term (required for action=search)
        project:    absolute project path filter (e.g. "/Users/cankone/Development/UML/Memoli")
        session_id: required for action=show
        source:     filter by source agent (currently only "claude")
        limit:      max rows returned
    """
    import sys as _sys
    from pathlib import Path as _Path
    _here = _Path(__file__).parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    import chorus_recall as cr

    loop = asyncio.get_event_loop()

    def _run():
        conn = cr.db_connect()
        try:
            cr.index_claude(conn, verbose=False)
            ns = type("A", (), {})()
            ns.json = True
            ns.pretty = False
            ns.project = project or None
            ns.source = source or None
            ns.limit = limit
            ns.query = query
            ns.session_id = session_id

            buf = []
            class _W:
                def write(self, s): buf.append(s)
            real_stdout = _sys.stdout
            _sys.stdout = _W()
            try:
                if action == "files":
                    cr.cmd_files(conn, ns)
                elif action == "list":
                    cr.cmd_list(conn, ns)
                elif action == "search":
                    if not query:
                        return '{"error":"query required for action=search"}'
                    cr.cmd_search(conn, ns)
                elif action == "show":
                    if not session_id:
                        return '{"error":"session_id required for action=show"}'
                    cr.cmd_show(conn, ns)
                elif action == "health":
                    cr.cmd_health(conn, ns)
                else:
                    return f'{{"error":"unknown action: {action}. Use files|list|search|show|health"}}'
            finally:
                _sys.stdout = real_stdout
            return "".join(buf).strip() or "[]"
        finally:
            conn.close()

    return await loop.run_in_executor(None, _run)


if __name__ == "__main__":
    mcp.run(transport="stdio")
