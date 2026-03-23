"""Conductor - an LLM that orchestrates other LLMs, talks to the user naturally."""

import json
import logging
import queue
import re
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status

from chorus.config import get_provider_config
from chorus.memory import Memory
from chorus.models import LLMResponse, Message
from chorus.providers.base import get_available_providers, get_provider

logger = logging.getLogger(__name__)
console = Console()

CONDUCTOR_SYSTEM = """You are the Conductor of Chorus — a multi-LLM deliberation system.
Your job is to orchestrate conversations between multiple AI models and help the user get the best possible answers.

You have these tools available (use JSON blocks to call them):

1. **ask_all** — Ask multiple models the same question in parallel (FAST):
```tool
{{"tool": "ask_all", "prompt": "the question", "exclude": ["copilot"]}}
```
The "exclude" field is optional. Use it to skip specific models. If omitted, all models are asked.

2. **ask_one** — Ask a SINGLE specific model (only when you need exactly one model):
```tool
{{"tool": "ask_one", "model": "claude|gemini|copilot|codex", "prompt": "the question"}}
```

3. **cross_send** — Send one model's response to another for critique:
```tool
{{"tool": "cross_send", "from": "claude", "to": "gemini", "comment": "optional specific question"}}
```

4. **search_memory** — Search past conversations:
```tool
{{"tool": "search_memory", "query": "search terms"}}
```

5. **activate_agent** — Run a specialized agent for a specific task:
```tool
{{"tool": "activate_agent", "agent": "agent-name", "context": "optional extra context from user"}}
```
The "context" field is optional. It provides additional user context to the agent's predefined prompt.

## Available models: {available_models}
{available_agents}

## How to behave:
- When the user asks a question, decide if it needs one model or multiple
- For factual/simple questions → ask one model
- For opinions, comparisons, decisions, complex topics → ask multiple models
- Always tell the user what you're about to do BEFORE calling tools
- After getting results, summarize the key points concisely
- If models disagree, highlight the disagreement and offer to run a deeper debate
- Speak in the same language as the user
- Be concise and natural — you're a helpful conductor, not a bureaucrat
- You can call multiple tools in sequence — first ask_all, then cross_send based on results
- CRITICAL: When asking 2+ models, ALWAYS use ask_all (with exclude if needed) or activate_agent. NEVER call ask_one multiple times — that runs sequentially and is very slow.
- Prefer activate_agent when a specialized agent matches the user's request. Use ask_all/ask_one for ad-hoc questions that no agent covers.
- IMPORTANT: Write your message to the user FIRST, then put tool calls at the END of your response
- Only use ONE tool call per response. Do not put multiple tool blocks.
- NEVER read files, browse directories, or use CLI tools yourself. You are the CONDUCTOR — you only delegate work to other models via ask_all/ask_one. The other models will read files and do the actual work."""


class Conductor:
    """LLM-powered orchestrator that naturally manages multi-model interactions."""

    def __init__(self, memory: Memory, session_id: str, cwd: Optional[str] = None, agents: dict = None, conductor_override=None):
        self.memory = memory
        self.session_id = session_id
        self.cwd = cwd
        self.agents = agents or {}
        self.conductor_override = conductor_override
        self.conductor_session_id: Optional[str] = None
        self.user_queue: queue.Queue = queue.Queue()  # For mid-execution user input

    def _conductor_generate_standalone(self, prompt: str) -> LLMResponse:
        """Standalone conductor call WITHOUT --resume. Used for session summaries."""
        import os
        import subprocess
        import time

        config = get_provider_config("claude") or {}
        cli_cmd = config.get("cli_command", "claude")
        model = self._get_model_for("claude")

        cmd = [
            cli_cmd, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--dangerously-skip-permissions",
            "--max-turns", "1",
        ]
        # No --resume: completely independent call

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        start = time.time()
        try:
            import json as _json
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=60, cwd=self.cwd, env=env,  # Short timeout for summaries
            )
            duration_ms = int((time.time() - start) * 1000)

            if result.returncode != 0:
                error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
                return LLMResponse(text="", model=model, provider="claude", error=error_msg, duration_ms=duration_ms)

            data = _json.loads(result.stdout)
            text = data.get("result", data.get("text", ""))
            if not text or not str(text).strip():
                text = result.stdout.strip()

            return LLMResponse(text=str(text), model=model, provider="claude", duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            return LLMResponse(text="", model=model, provider="claude", error="Summary timeout")
        except Exception as e:
            return LLMResponse(text="", model=model, provider="claude", error=str(e))

    def generate_session_summary(self) -> Optional[str]:
        """Generate and save a summary for the current session."""
        msg_count = self.memory.get_message_count(self.session_id)
        if msg_count < 4:
            return None

        messages = self.memory.get_session_messages(self.session_id, limit=30)
        if not messages:
            return None

        # Build compact transcript
        lines = []
        for m in messages:
            speaker = m.provider or m.role
            lines.append(f"[{speaker}]: {m.content[:200]}")
        transcript = "\n".join(lines)

        summary_prompt = f"""You are summarizing a multi-LLM conversation for future reference.

Conversation transcript:
{transcript}

Write a concise 2-3 sentence summary covering: main topics, key conclusions, which models participated.
Then list 5-8 key topics as comma-separated keywords.
Respond in the SAME LANGUAGE the user was speaking in the conversation.

Format exactly like this:
SUMMARY: <your summary>
TOPICS: <comma-separated keywords>"""

        response = self._conductor_generate_standalone(summary_prompt)
        if response.error or not response.text:
            return None

        # Parse SUMMARY and TOPICS
        text = response.text
        summary = ""
        topics = ""

        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("SUMMARY:"):
                summary = line[8:].strip()
            elif line.upper().startswith("TOPICS:"):
                topics = line[7:].strip()

        if not summary:
            # Fallback: use the whole response as summary
            summary = text[:500]

        self.memory.save_session_summary(self.session_id, summary, topics, msg_count)
        return summary

    def _conductor_generate(self, prompt: str) -> LLMResponse:
        """Call conductor LLM with max_turns=1 so it only generates text, no tool use."""
        import os
        import subprocess
        import time

        config = get_provider_config("claude") or {}
        cli_cmd = config.get("cli_command", "claude")
        timeout = config.get("timeout", 300)
        model = self._get_model_for("claude")

        cmd = [
            cli_cmd, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--dangerously-skip-permissions",
            "--max-turns", "1",  # Key: only 1 turn, no tool use
        ]
        if self.conductor_session_id:
            cmd.extend(["--resume", self.conductor_session_id])

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        start = time.time()
        try:
            import json as _json
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=self.cwd, env=env,
            )
            duration_ms = int((time.time() - start) * 1000)

            # Try to parse JSON first (even if returncode != 0, stdout may have valid JSON)
            data = None
            if result.stdout and result.stdout.strip():
                try:
                    data = _json.loads(result.stdout)
                except _json.JSONDecodeError:
                    pass

            if data:
                text = data.get("result", data.get("text", ""))
                session_id = data.get("session_id", self.conductor_session_id)
                subtype = data.get("subtype", "")

                # Handle max_turns error — conductor tried to use tools but couldn't
                if subtype == "error_max_turns":
                    return LLMResponse(
                        text="", model=model, provider="claude",
                        error="Conductor tried to use tools. Retrying with clearer instructions.",
                        session_id=session_id, duration_ms=duration_ms,
                    )

                if text and str(text).strip():
                    return LLMResponse(text=str(text), model=model, provider="claude", session_id=session_id, duration_ms=duration_ms)

            # No valid JSON or empty text — check returncode
            if result.returncode != 0:
                error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
                return LLMResponse(text="", model=model, provider="claude", error=error_msg, duration_ms=duration_ms)

            # Success but empty
            return LLMResponse(
                text="", model=model, provider="claude",
                error="Empty response from conductor",
                duration_ms=duration_ms,
            )

            return LLMResponse(text=str(text), model=model, provider="claude", session_id=session_id, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="claude", error=f"Timeout after {timeout}s", duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="claude", error=str(e), duration_ms=duration_ms)

    def _get_model_for(self, provider_name: str) -> str:
        config = get_provider_config(provider_name) or {}
        return config.get("model", "default")

    def _get_display_name(self, provider_name: str) -> str:
        config = get_provider_config(provider_name) or {}
        return config.get("display_name", provider_name)

    def _build_system_prompt(self) -> str:
        available = get_available_providers()
        model_list = []
        for name in available:
            display = self._get_display_name(name)
            model = self._get_model_for(name)
            model_list.append(f"- {name} ({display}, model: {model})")

        # Build agent list section
        agent_section = ""
        if self.agents:
            lines = ["## Available agents (use activate_agent to run them):"]
            for agent in self.agents.values():
                mode_info = f"mode: {agent.mode}"
                if agent.mode == "debate":
                    mode_info += f", {agent.rounds} rounds"
                lines.append(f"- **{agent.name}**: {agent.description} ({mode_info})")
            lines.append("\nPrefer activate_agent when a specialized agent matches. Use ask_all/ask_one for ad-hoc questions.")
            agent_section = "\n".join(lines)

        # Use conductor override or default system prompt
        if self.conductor_override:
            base_prompt = self.conductor_override.prompt_body
            # Ensure tool definitions are present by appending model/agent info
            base_prompt += f"\n\n## Available models:\n" + "\n".join(model_list)
            if agent_section:
                base_prompt += f"\n\n{agent_section}"
            prompt = base_prompt
        else:
            prompt = CONDUCTOR_SYSTEM.format(
                available_models="\n".join(model_list),
                available_agents=agent_section,
            )

        # Inject recent session summaries for cross-session awareness
        summaries = self.memory.get_recent_summaries(limit=5, exclude_session_id=self.session_id)
        if summaries:
            prompt += "\n\n## Recent conversation history:\nYou remember these past sessions. Use them to provide continuity.\n\n"
            for s in summaries:
                date = s["created_at"][:10] if s.get("created_at") else "?"
                title = s.get("title", "Untitled")
                msg_count = s.get("message_count", 0)
                summary = s.get("summary", "")
                topics = s.get("key_topics", "")
                prompt += f'- [{date}] "{title}" ({msg_count} msgs): {summary}'
                if topics:
                    prompt += f" Topics: {topics}"
                prompt += "\n"

        return prompt

    def _extract_tool_calls(self, text: str) -> tuple[str, list[dict]]:
        """Extract tool call JSON blocks from conductor's response."""
        tool_calls = []
        clean_text = text

        # Match ```tool ... ``` blocks
        pattern = r'```tool\s*\n?(.*?)\n?```'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                tool_call = json.loads(match.strip())
                tool_calls.append(tool_call)
            except json.JSONDecodeError:
                continue

        # Remove tool blocks from display text
        clean_text = re.sub(pattern, '', text, flags=re.DOTALL).strip()

        # Also try inline JSON tool calls (fallback)
        if not tool_calls:
            inline_pattern = r'\{"tool":\s*"[^"]+?"[^}]*\}'
            inline_matches = re.findall(inline_pattern, text)
            for match in inline_matches:
                try:
                    tool_call = json.loads(match)
                    tool_calls.append(tool_call)
                    clean_text = clean_text.replace(match, '').strip()
                except json.JSONDecodeError:
                    continue

        return clean_text, tool_calls

    def _execute_tool(self, tool_call: dict) -> str:
        """Execute a tool call and return the result as text."""
        tool_name = tool_call.get("tool", "")

        if tool_name == "ask_all":
            prompt = tool_call.get("prompt", "")
            exclude = tool_call.get("exclude", [])
            return self._tool_ask_all(prompt, exclude=exclude)

        elif tool_name == "ask_one":
            model = tool_call.get("model", "")
            prompt = tool_call.get("prompt", "")
            return self._tool_ask_one(model, prompt)

        elif tool_name == "cross_send":
            from_p = tool_call.get("from", "")
            to_p = tool_call.get("to", "")
            comment = tool_call.get("comment", "")
            return self._tool_cross_send(from_p, to_p, comment)

        elif tool_name == "search_memory":
            query = tool_call.get("query", "")
            return self._tool_search_memory(query)

        elif tool_name == "activate_agent":
            agent_name = tool_call.get("agent", "")
            context = tool_call.get("context", "")
            return self._tool_activate_agent(agent_name, context)

        return f"Unknown tool: {tool_name}"

    def _tool_ask_all(self, prompt: str, exclude: list[str] = None) -> str:
        """Ask available models in parallel, optionally excluding some."""
        available = get_available_providers()
        if exclude:
            available = {k: v for k, v in available.items() if k not in exclude}
        if not available:
            return "No models available."

        self.memory.save_message(self.session_id, Message(role="user", content=prompt))

        results: dict[str, LLMResponse] = {}

        def _call(name: str):
            provider = available[name]
            model = self._get_model_for(name)
            # Use "participant_<name>" key to avoid collision with conductor's session
            cli_session = self.memory.get_provider_session(self.session_id, f"participant_{name}")
            # For ask_all, we pass the prompt directly — models decide how deep to go
            resp = provider.generate(prompt, model, session_id=cli_session, cwd=self.cwd)
            return name, resp

        model_names = {n: self._get_display_name(n) for n in available}

        pending = set(available.keys())
        completed_summaries = []
        start_time = _time.time()
        stop_ticker = threading.Event()
        skip_remaining = threading.Event()

        def _conductor_brief(status_prompt: str):
            """Quick conductor call for natural status update."""
            try:
                resp = self._conductor_generate(status_prompt)
                if resp.text and not resp.error:
                    text, _ = self._extract_tool_calls(resp.text)
                    if text:
                        console.print(Panel(text.strip(), border_style="blue", padding=(0, 1)))
            except Exception:
                pass

        def _check_user_input():
            """Check if user typed something during tool execution."""
            try:
                msg = self.user_queue.get_nowait()
                if not msg:
                    return
                lower = msg.lower().strip()
                # Skip/cancel commands
                if lower in ("skip", "atla", "devam", "devam et", "continue", "s"):
                    console.print(f"[yellow]  → Skipping remaining models...[/yellow]")
                    skip_remaining.set()
                    stop_ticker.set()
                    return
                # Any other message: pass to conductor for a brief response
                elapsed = int(_time.time() - start_time)
                waiting = ", ".join(model_names[n] for n in pending)
                done_names = [model_names[n] for n in available if n not in pending]
                done_str = ", ".join(done_names) if done_names else "none yet"
                _conductor_brief(
                    f"USER MESSAGE during tool execution: \"{msg}\". "
                    f"Status: {elapsed}s elapsed, done: {done_str}, waiting: {waiting}. "
                    f"Respond naturally to the user's message considering the current status. "
                    f"Match the user's language. No tools."
                )
            except queue.Empty:
                pass

        def _ticker():
            """Periodically check user input and provide natural waiting updates."""
            tick_count = 0
            while not stop_ticker.is_set():
                stop_ticker.wait(5)  # Check user input every 5s
                if stop_ticker.is_set() or not pending:
                    break
                _check_user_input()
                tick_count += 1
                # Conductor status update every 25s (every 5th tick)
                if tick_count % 5 == 0:
                    elapsed = int(_time.time() - start_time)
                    waiting = ", ".join(model_names[n] for n in pending)
                    done_names = [model_names[n] for n in available if n not in pending]
                    done_str = ", ".join(done_names) if done_names else "none yet"
                    _conductor_brief(
                        f"STATUS UPDATE REQUEST: {elapsed}s elapsed. Done: {done_str}. Still working: {waiting}. "
                        f"Give user a brief natural 1-sentence update about the wait. Match the user's language. No tools."
                    )

        ticker_thread = threading.Thread(target=_ticker, daemon=True)
        ticker_thread.start()

        with ThreadPoolExecutor(max_workers=len(available)) as pool:
            futures = {pool.submit(_call, name): name for name in available}
            for future in as_completed(futures):
                if skip_remaining.is_set():
                    break
                try:
                    name, response = future.result()
                    results[name] = response
                    pending.discard(name)
                    display = model_names[name]

                    if response.error:
                        console.print(f"[red]  ✗ {display}: {response.error[:100]}[/red]")
                        completed_summaries.append(f"{display}: ERROR")
                    else:
                        duration_s = response.duration_ms / 1000 if response.duration_ms else 0
                        console.print(f"[green]  ✓ {display} ({duration_s:.0f}s)[/green]")
                        self.memory.save_message(self.session_id, Message(
                            role="assistant", content=response.text, provider=name,
                            model=response.model, duration_ms=response.duration_ms,
                        ))
                        summary = response.text[:300].replace('\n', ' ')
                        completed_summaries.append(f"{display}: {summary}")

                        # Ask conductor to comment + check user input
                        _check_user_input()
                        if pending and not skip_remaining.is_set():
                            waiting = ", ".join(model_names[n] for n in pending)
                            _conductor_brief(
                                f"MODEL COMPLETED: {display} responded in {duration_s:.0f}s. Summary: {summary}... "
                                f"Still waiting: {waiting}. "
                                f"Give user a natural 1-2 sentence update: what did {display} say (key point) + who's still working. "
                                f"Match user's language. No tools."
                            )

                    if response.session_id:
                        self.memory.update_provider_session(self.session_id, f"participant_{name}", response.session_id)
                except Exception as e:
                    pname = futures[future]
                    pending.discard(pname)
                    console.print(f"[red]  ✗ {pname}: {e}[/red]")

        stop_ticker.set()
        ticker_thread.join(timeout=1)

        # Build result text for conductor
        parts = []
        for name, resp in results.items():
            display = self._get_display_name(name)
            if resp.text and not resp.error:
                parts.append(f"**{display}** ({resp.model}, {resp.duration_ms}ms):\n{resp.text}")
            elif resp.error:
                parts.append(f"**{display}**: ERROR - {resp.error}")
        return "\n\n---\n\n".join(parts)

    def _tool_ask_one(self, provider_name: str, prompt: str) -> str:
        """Ask a single model."""
        provider = get_provider(provider_name)
        if not provider:
            return f"Provider '{provider_name}' not found."

        display = self._get_display_name(provider_name)
        console.print(f"[dim]  Asking {display}...[/dim]")

        model = self._get_model_for(provider_name)
        cli_session = self.memory.get_provider_session(self.session_id, f"participant_{provider_name}")

        self.memory.save_message(self.session_id, Message(role="user", content=prompt, provider=provider_name))
        response = provider.generate(prompt, model, session_id=cli_session, cwd=self.cwd)

        if response.text and not response.error:
            duration = f" ({response.duration_ms/1000:.1f}s)" if response.duration_ms else ""
            console.print(f"[green]  ✓ {display}{duration}[/green]")
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=response.text, provider=provider_name,
                model=response.model, duration_ms=response.duration_ms,
            ))
        elif response.error:
            console.print(f"[red]  ✗ {display}: {response.error[:100]}[/red]")

        if response.session_id:
            self.memory.update_provider_session(self.session_id, f"participant_{provider_name}", response.session_id)

        return f"**{display}** ({response.model}):\n{response.text}" if response.text else f"**{display}**: ERROR - {response.error}"

    def _tool_cross_send(self, from_p: str, to_p: str, comment: str = "") -> str:
        """Send one model's last response to another."""
        messages = self.memory.get_session_messages(self.session_id, limit=50)
        source_msg = None
        for m in reversed(messages):
            if m.provider == from_p and m.role == "assistant":
                source_msg = m
                break

        if not source_msg:
            return f"No recent response from {from_p}."

        from_display = self._get_display_name(from_p)
        to_display = self._get_display_name(to_p)

        cross_prompt = f"""{from_display} said:

---
{source_msg.content}
---

{comment if comment else f"What is your perspective? Do you agree, disagree, or want to add nuance?"}"""

        provider = get_provider(to_p)
        if not provider:
            return f"Provider '{to_p}' not found."

        console.print(f"[dim]  Sending {from_display}'s response to {to_display}...[/dim]")

        model = self._get_model_for(to_p)
        cli_session = self.memory.get_provider_session(self.session_id, f"participant_{to_p}")

        response = provider.generate(cross_prompt, model, session_id=cli_session, cwd=self.cwd)

        if response.text and not response.error:
            console.print(f"[green]  ✓ {to_display} responded[/green]")
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=response.text, provider=to_p,
                model=response.model, duration_ms=response.duration_ms,
                cross_from=from_p,
            ))
        if response.session_id:
            self.memory.update_provider_session(self.session_id, f"participant_{to_p}", response.session_id)

        return f"**{to_display}** (responding to {from_display}):\n{response.text}" if response.text else f"ERROR: {response.error}"

    def _tool_search_memory(self, query: str) -> str:
        """Search past conversations — both session summaries and individual messages."""
        parts = []

        # First: search session summaries (high-level context)
        summaries = self.memory.search_summaries(query, limit=3)
        if summaries:
            parts.append("=== SESSION SUMMARIES ===")
            for s in summaries:
                date = s.get("created_at", "")[:10]
                title = s.get("title", "Untitled")
                parts.append(f"[Session: {title}, {date}] {s['summary']} (Topics: {s.get('key_topics', '')})")

        # Then: search individual messages (detail)
        results = self.memory.search(query, limit=5)
        if results:
            parts.append("\n=== MESSAGES ===")
            for r in results:
                parts.append(f"[{r.get('provider', '?')}] ({r.get('timestamp', '')[:16]}): {r['content'][:300]}")

        if not parts:
            return "No results found in memory."
        return "\n\n".join(parts)

    def _tool_activate_agent(self, agent_name: str, context: str = "") -> str:
        """Activate a specialized agent by name."""
        agent = self.agents.get(agent_name)
        if not agent:
            available = ", ".join(self.agents.keys()) if self.agents else "none"
            return f"Agent '{agent_name}' not found. Available agents: {available}"

        console.print(f"[dim]  Activating agent: [bold]{agent.name}[/bold] (mode: {agent.mode})[/dim]")

        from chorus.agents import AgentExecutor
        executor = AgentExecutor(self)
        return executor.execute(agent, context)

    def chat(self, user_message: str) -> str:
        """Main entry point — user sends message, conductor handles everything."""
        # Save user message
        self.memory.save_message(self.session_id, Message(role="user", content=user_message))

        # Build prompt for conductor
        recent_context = self.memory.get_recent_context(self.session_id, max_messages=20)
        system_prompt = self._build_system_prompt()

        if recent_context:
            full_prompt = f"""{system_prompt}

## Recent conversation context:
{recent_context}

## User message:
{user_message}"""
        else:
            full_prompt = f"""{system_prompt}

## User message:
{user_message}"""

        # Call conductor LLM (Claude with max_turns=1)
        with Status("[bold blue]Chorus is thinking...[/bold blue]", console=console, spinner="dots"):
            response = self._conductor_generate(full_prompt)

        # If conductor tried to use tools (max_turns hit), retry with standalone call
        if response.error and "Retrying" in response.error:
            # Build a shorter, more direct prompt without resume
            available = get_available_providers()
            model_list = ", ".join(available.keys())
            agent_list = ", ".join(self.agents.keys()) if self.agents else "none"

            retry_prompt = f"""You are the Chorus conductor. You MUST NOT use any CLI tools or read files.
You can ONLY respond with text and ONE tool call block.

Available tools: ask_all, ask_one, activate_agent, cross_send, search_memory
Available models: {model_list}
Available agents: {agent_list}

The user said: {user_message}

Decide which tool to use and respond. Example:
"I'll ask all models to research this topic."
```tool
{{"tool": "ask_all", "prompt": "your prompt here"}}
```"""

            with Status("[bold blue]Chorus is re-thinking...[/bold blue]", console=console, spinner="dots"):
                response = self._conductor_generate_standalone(retry_prompt)

            if response.session_id:
                self.conductor_session_id = response.session_id

        if response.error:
            console.print(f"[red]Conductor error: {response.error}[/red]")
            return f"Conductor error: {response.error}"

        if response.session_id:
            self.conductor_session_id = response.session_id

        # Extract tool calls from conductor's response
        display_text, tool_calls = self._extract_tool_calls(response.text)

        # Safety: don't show raw JSON to user
        if display_text and display_text.strip().startswith('{"type"'):
            display_text = ""

        # Show conductor's message to user
        if display_text:
            console.print()
            console.print(Panel(
                Markdown(display_text),
                title="[bold blue]Chorus[/bold blue]",
                border_style="blue",
            ))

        # Execute tool calls
        if tool_calls:
            tool_results = []
            for tc in tool_calls:
                result = self._execute_tool(tc)
                tool_results.append(result)

            # Feed results back to conductor for synthesis
            results_text = "\n\n===\n\n".join(tool_results)
            synthesis_prompt = f"""Here are the results from the tools you called:

{results_text}

Now provide a clear, concise summary for the user. Highlight key agreements, disagreements, and insights. Speak in the same language as the user. Do NOT call any more tools."""

            with Status("[bold blue]Chorus is synthesizing...[/bold blue]", console=console, spinner="dots"):
                synth_response = self._conductor_generate(synthesis_prompt)

            if synth_response.session_id:
                self.conductor_session_id = synth_response.session_id

            if synth_response.text:
                # Clean any accidental tool calls from synthesis
                synth_text, _ = self._extract_tool_calls(synth_response.text)
                console.print()
                console.print(Panel(
                    Markdown(synth_text),
                    title="[bold blue]Chorus — Synthesis[/bold blue]",
                    border_style="blue",
                ))
                # Save synthesis to memory
                self.memory.save_message(self.session_id, Message(
                    role="assistant", content=synth_text, provider="chorus",
                    model=response.model,
                ))
                return synth_text

        # Save conductor's direct response to memory
        if display_text:
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=display_text, provider="chorus",
                model=response.model,
            ))

        return display_text or ""
