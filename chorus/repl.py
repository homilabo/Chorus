"""Interactive REPL - the main user interface for Chorus."""

import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from chorus.config import ensure_config, load_config
from chorus.memory import Memory
from chorus.providers import init_providers
from chorus.providers.base import get_available_providers

console = Console()

MANUAL_HELP = """
[bold]Commands:[/bold]
  [cyan]/all[/cyan] <prompt>        Ask all models the same question
  [cyan]/ask[/cyan] <model> <prompt> Ask a specific model (claude, gemini, copilot, codex)
  [cyan]/cross[/cyan] <from> <to>    Send one model's last response to another
  [cyan]/debate[/cyan] <rounds>      Start a multi-round debate (default: 2 rounds)
  [cyan]/synth[/cyan]               Synthesize all perspectives
  [cyan]/memory[/cyan] <query>       Search past conversations
  [cyan]/sessions[/cyan]            List past sessions
  [cyan]/models[/cyan]              Show available models
  [cyan]/help[/cyan]                Show this help
  [cyan]/quit[/cyan]                Exit

[bold]Quick syntax:[/bold]
  @claude <prompt>     → shortcut for /ask claude <prompt>
  @gemini <prompt>     → shortcut for /ask gemini <prompt>
  Just type anything   → sends to default model (claude)
"""


def show_response(provider: str, response, display_name: str = ""):
    """Display a single model response."""
    name = display_name or provider
    if response.error:
        console.print(f"[red]{name}[/red]: Error - {response.error}")
        return
    duration = f" ({response.duration_ms}ms)" if response.duration_ms else ""
    console.print()
    console.print(Panel(
        Markdown(response.text),
        title=f"[bold]{name}[/bold] [{response.model}]{duration}",
        border_style="cyan" if provider == "claude" else "green" if provider == "gemini" else "yellow" if provider == "copilot" else "magenta",
    ))


def show_broadcast(results: dict, orchestrator):
    """Display broadcast results from multiple models."""
    for provider_name, response in results.items():
        display = orchestrator._get_display_name(provider_name)
        show_response(provider_name, response, display)


def _exit_session(conductor, memory, console_obj):
    """Generate session summary on exit, then close memory."""
    try:
        console_obj.print("[dim]Generating session summary...[/dim]")
        summary = conductor.generate_session_summary()
        if summary:
            console_obj.print(f"[dim]Session summarized.[/dim]")
        else:
            console_obj.print("[dim]Session too short to summarize.[/dim]")
    except KeyboardInterrupt:
        console_obj.print("[dim]Summary skipped.[/dim]")
    except Exception:
        console_obj.print("[dim]Summary skipped.[/dim]")
    finally:
        memory.close()


# ─── Conductor Mode (default) ───

def run_conductor(cwd: Optional[str] = None):
    """Conductor mode — an LLM orchestrates everything naturally."""
    from chorus.conductor import Conductor
    from chorus.agents import load_agents, load_conductor_override, ensure_default_agents

    ensure_config()
    load_config()
    init_providers()
    ensure_default_agents()

    available = get_available_providers()
    if not available:
        console.print("[red]No providers available. Install at least one CLI tool (claude, gemini, copilot, codex).[/red]")
        sys.exit(1)

    # Load agents and conductor override
    agents = load_agents(cwd=cwd)
    conductor_override = load_conductor_override(cwd=cwd)

    memory = Memory()
    session = memory.create_session()
    conductor = Conductor(memory, session.id, cwd=cwd, agents=agents, conductor_override=conductor_override)

    provider_names = ", ".join(f"[bold]{conductor._get_display_name(n)}[/bold]" for n in available)
    agent_names = ", ".join(agents.keys()) if agents else "none"
    cwd_display = cwd or "."
    console.print(Panel(
        f"[bold]Chorus v0.1.0[/bold] — Multi-LLM Deliberation\n"
        f"[blue]Conductor mode[/blue] — just talk naturally\n"
        f"Models: {provider_names}\n"
        f"Agents: {agent_names}\n"
        f"Working dir: {cwd_display}\n"
        f"Session: {session.id}\n"
        f"Type [cyan]/quit[/cyan] to exit, [cyan]/agents[/cyan] to list agents",
        border_style="blue",
    ))

    while True:
        try:
            console.print()
            user_input = console.input("[bold blue]chorus>[/bold blue] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input in ("/quit", "/exit", "/q"):
            console.print("[dim]Goodbye.[/dim]")
            break

        # Minimal commands that bypass conductor
        if user_input.startswith("/memory "):
            query = user_input[8:].strip()
            if not query:
                console.print("[dim]Usage: /memory <search query>[/dim]")
                continue
            results = memory.search(query)
            if not results:
                console.print("[dim]No results found.[/dim]")
                continue
            for r in results:
                console.print(Panel(
                    f"{r['content'][:300]}",
                    title=f"[bold]{r.get('provider', '?')}[/bold] — {r.get('title', '')} ({r.get('timestamp', '')[:16]})",
                    border_style="dim",
                ))
            continue

        if user_input == "/sessions":
            sessions = memory.list_sessions()
            if not sessions:
                console.print("[dim]No past sessions.[/dim]")
                continue
            table = Table(title="Sessions")
            table.add_column("ID")
            table.add_column("Title")
            table.add_column("Updated")
            for s in sessions:
                table.add_row(s["id"], s["title"], s["updated_at"][:16])
            console.print(table)
            continue

        if user_input == "/agents":
            if not agents:
                console.print("[dim]No agents loaded.[/dim]")
                continue
            table = Table(title="Available Agents")
            table.add_column("Name", style="bold")
            table.add_column("Description")
            table.add_column("Mode")
            table.add_column("Source")
            for agent in agents.values():
                source = "project" if "chorus-agents" in agent.source_path else "global"
                mode = agent.mode
                if agent.mode == "debate":
                    mode += f" ({agent.rounds}r)"
                table.add_row(agent.name, agent.description, mode, source)
            console.print(table)
            continue

        if user_input == "/models":
            table = Table(title="Available Models")
            table.add_column("Provider", style="bold")
            table.add_column("Default Model")
            table.add_column("Status")
            for name in available:
                display = conductor._get_display_name(name)
                model = conductor._get_model_for(name)
                table.add_row(display, model, "[green]ready[/green]")
            console.print(table)
            continue

        # Everything else goes to conductor
        conductor.chat(user_input)

    _exit_session(conductor, memory, console)


# ─── Manual Mode (--manual) ───

def run_manual(cwd: Optional[str] = None):
    """Manual command mode — /all, /debate, /cross, etc."""
    from chorus.orchestrator import Orchestrator

    ensure_config()
    load_config()
    init_providers()

    available = get_available_providers()
    if not available:
        console.print("[red]No providers available. Install at least one CLI tool (claude, gemini, copilot, codex).[/red]")
        sys.exit(1)

    memory = Memory()
    session = memory.create_session()
    orchestrator = Orchestrator(memory, session.id, cwd=cwd)

    provider_names = ", ".join(f"[bold]{orchestrator._get_display_name(n)}[/bold]" for n in available)
    console.print(Panel(
        f"[bold]Chorus v0.1.0[/bold] — Multi-LLM Deliberation\n"
        f"[yellow]Manual mode[/yellow] — use /commands\n"
        f"Active models: {provider_names}\n"
        f"Session: {session.id}\n"
        f"Type [cyan]/help[/cyan] for commands, [cyan]/quit[/cyan] to exit",
        border_style="blue",
    ))

    while True:
        try:
            console.print()
            user_input = console.input("[bold yellow]chorus>[/bold yellow] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input in ("/quit", "/exit", "/q"):
            console.print("[dim]Goodbye.[/dim]")
            break

        if user_input == "/help":
            console.print(MANUAL_HELP)
            continue

        if user_input == "/models":
            table = Table(title="Available Models")
            table.add_column("Provider", style="bold")
            table.add_column("Default Model")
            table.add_column("Status")
            for name, provider in get_available_providers().items():
                display = orchestrator._get_display_name(name)
                model = orchestrator._get_model_for(name)
                table.add_row(display, model, "[green]ready[/green]")
            console.print(table)
            continue

        if user_input == "/sessions":
            sessions = memory.list_sessions()
            if not sessions:
                console.print("[dim]No past sessions.[/dim]")
                continue
            table = Table(title="Sessions")
            table.add_column("ID")
            table.add_column("Title")
            table.add_column("Updated")
            for s in sessions:
                table.add_row(s["id"], s["title"], s["updated_at"][:16])
            console.print(table)
            continue

        if user_input.startswith("/memory "):
            query = user_input[8:].strip()
            if not query:
                console.print("[dim]Usage: /memory <search query>[/dim]")
                continue
            results = memory.search(query)
            if not results:
                console.print("[dim]No results found.[/dim]")
                continue
            for r in results:
                console.print(Panel(
                    f"{r['content'][:300]}",
                    title=f"[bold]{r.get('provider', '?')}[/bold] — {r.get('title', '')} ({r.get('timestamp', '')[:16]})",
                    border_style="dim",
                ))
            continue

        if user_input.startswith("/all "):
            prompt = user_input[5:].strip()
            if not prompt:
                console.print("[dim]Usage: /all <your question>[/dim]")
                continue
            console.print(f"[dim]Broadcasting to {len(available)} models...[/dim]")
            results = orchestrator.broadcast(prompt)
            show_broadcast(results, orchestrator)
            continue

        if user_input.startswith("/ask "):
            parts = user_input[5:].strip().split(" ", 1)
            if len(parts) < 2:
                console.print("[dim]Usage: /ask <model> <prompt>[/dim]")
                continue
            provider_name, prompt = parts
            if provider_name not in available:
                console.print(f"[red]'{provider_name}' not available. Choose from: {', '.join(available)}[/red]")
                continue
            display = orchestrator._get_display_name(provider_name)
            console.print(f"[dim]Asking {display}...[/dim]")
            response = orchestrator.ask(provider_name, prompt)
            show_response(provider_name, response, display)
            continue

        if user_input.startswith("/cross "):
            parts = user_input[7:].strip().split(" ", 2)
            if len(parts) < 2:
                console.print("[dim]Usage: /cross <from> <to> [comment][/dim]")
                continue
            from_p, to_p = parts[0], parts[1]
            comment = parts[2] if len(parts) > 2 else ""
            for p in (from_p, to_p):
                if p not in available:
                    console.print(f"[red]'{p}' not available. Choose from: {', '.join(available)}[/red]")
                    break
            else:
                from_display = orchestrator._get_display_name(from_p)
                to_display = orchestrator._get_display_name(to_p)
                console.print(f"[dim]Sending {from_display}'s response to {to_display}...[/dim]")
                response = orchestrator.cross_send(from_p, to_p, comment)
                show_response(to_p, response, to_display)
            continue

        if user_input.startswith("/debate"):
            parts = user_input.split()
            rounds = 2
            prompt_start = 1
            if len(parts) > 1 and parts[1].isdigit():
                rounds = int(parts[1])
                prompt_start = 2

            remaining = " ".join(parts[prompt_start:]).strip()
            if not remaining:
                console.print("[dim]Enter the debate topic:[/dim]")
                try:
                    remaining = console.input("[bold yellow]topic>[/bold yellow] ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not remaining:
                    continue

            console.print(f"[dim]Starting {rounds}-round debate with {len(available)} models...[/dim]")
            all_rounds = orchestrator.debate(remaining, rounds)

            for i, round_results in enumerate(all_rounds):
                console.print(f"\n[bold]━━━ Round {i + 1} ━━━[/bold]")
                show_broadcast(round_results, orchestrator)
            continue

        if user_input.startswith("/synth"):
            parts = user_input.split()
            synth_model = parts[1] if len(parts) > 1 else None
            console.print("[dim]Synthesizing all perspectives...[/dim]")
            response = orchestrator.synthesize(synth_model)
            show_response(response.provider, response, "Synthesis")
            continue

        # @ shortcuts
        if user_input.startswith("@"):
            parts = user_input[1:].split(" ", 1)
            provider_name = parts[0]
            prompt = parts[1] if len(parts) > 1 else ""
            if provider_name not in available:
                console.print(f"[red]'{provider_name}' not available. Choose from: {', '.join(available)}[/red]")
                continue
            if not prompt:
                console.print(f"[dim]Usage: @{provider_name} <prompt>[/dim]")
                continue
            display = orchestrator._get_display_name(provider_name)
            console.print(f"[dim]Asking {display}...[/dim]")
            response = orchestrator.ask(provider_name, prompt)
            show_response(provider_name, response, display)
            continue

        # Default: send to first available
        default_provider = "claude" if "claude" in available else list(available.keys())[0]
        display = orchestrator._get_display_name(default_provider)
        console.print(f"[dim]Asking {display}...[/dim]")
        response = orchestrator.ask(default_provider, user_input)
        show_response(default_provider, response, display)

    memory.close()


# ─── Entry point ───

def run(cwd: Optional[str] = None, manual: bool = False):
    """Start the REPL in conductor or manual mode."""
    if manual:
        run_manual(cwd)
    else:
        run_conductor(cwd)
