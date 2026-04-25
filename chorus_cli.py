"""Command-line interface for Chorus provider calls."""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from cli import (
    CLIResult,
    call_claude,
    call_codex,
    call_copilot,
    call_gemini,
    set_session,
)
from config import get_role


AVAILABLE_PROVIDERS = {
    "gemini": call_gemini,
    "copilot": call_copilot,
    "codex": call_codex,
    "claude": call_claude,
}

ROOT_HELP = """\
Examples:
  chorus ask --provider copilot --agent-id worker-a "Review cli.py"
  chorus ask --role coder --json --output /tmp/coder.json "Implement the small fix"
  chorus parallel --tasks /tmp/tasks.json --json --output /tmp/results.json

Tasks JSON:
  [
    {"provider": "copilot", "agent_id": "worker-a", "prompt": "Find the project name"},
    {"role": "coder", "agent_id": "worker-b", "prompt": "Inspect dependencies"}
  ]
"""

ASK_HELP = """\
Examples:
  chorus ask --provider copilot "Summarize README.md"
  chorus ask --role reviewer --agent-id review-a --cwd /repo "Review cli.py"
  chorus ask --provider copilot --session <session-id> "Continue"
"""

PARALLEL_HELP = """\
Task fields:
  prompt       Required. Text sent to the model.
  provider     Optional. One of gemini, copilot, codex, claude.
  role         Optional. Configured role name, used when provider is omitted.
  agent_id     Optional. Isolates same-provider sessions.
  cwd          Optional. Overrides --cwd for this task.
  session      Optional. Resumes a specific session.

Examples:
  chorus parallel --tasks /tmp/tasks.json
  chorus parallel --tasks /tmp/tasks.json --max-workers 4 --json
  chorus parallel --tasks /tmp/tasks.json --json --output /tmp/results.json
"""


def _resolve_target(provider: str = "", role: str = "") -> tuple[str, str | None]:
    if role:
        role_cfg = get_role(role)
        if role_cfg:
            return role_cfg.get("provider", "gemini"), role_cfg.get("model")
        return role, None
    return provider or "gemini", None


def _session_key(provider: str, role: str = "", agent_id: str = "") -> str:
    if agent_id:
        return f"agent:{provider}:{agent_id}"
    return role or provider


def _prompt_from_args(parts: list[str]) -> str:
    prompt = " ".join(parts).strip()
    if not prompt:
        raise SystemExit("Prompt is required.")
    return prompt


def _result_dict(result: CLIResult, provider: str, role: str = "", agent_id: str = "") -> dict[str, Any]:
    return {
        "provider": provider,
        "role": role,
        "agent_id": agent_id,
        "text": result.text,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "session_id": result.session_id,
    }


def _call(prompt: str, provider: str = "", role: str = "", agent_id: str = "", cwd: str = ".", session: str = "") -> dict[str, Any]:
    provider, model = _resolve_target(provider, role)
    fn = AVAILABLE_PROVIDERS.get(provider)
    if not fn:
        return {
            "provider": provider,
            "role": role,
            "agent_id": agent_id,
            "text": "",
            "error": f"Unknown provider: {provider}",
            "duration_ms": 0,
            "session_id": None,
        }

    key = _session_key(provider, role, agent_id)
    if session:
        set_session(key, session)

    kwargs: dict[str, Any] = {"cwd": cwd, "session_key": key}
    if model:
        kwargs["model"] = model
    result = fn(prompt, **kwargs)
    return _result_dict(result, provider, role, agent_id)


def _write_output(payload: str, output: str = "") -> None:
    if output:
        Path(output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


def _format_ask(result: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(result, indent=2)
    return result["error"] or result["text"]


def _load_tasks(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise SystemExit("Tasks file must contain a JSON list or an object with a tasks list.")
    return tasks


def cmd_ask(args: argparse.Namespace) -> int:
    result = _call(
        _prompt_from_args(args.prompt),
        provider=args.provider,
        role=args.role,
        agent_id=args.agent_id,
        cwd=args.cwd,
        session=args.session,
    )
    _write_output(_format_ask(result, args.json), args.output)
    return 1 if result["error"] else 0


def cmd_parallel(args: argparse.Namespace) -> int:
    tasks = _load_tasks(args.tasks)
    results: list[dict[str, Any] | None] = [None] * len(tasks)
    workers = max(1, min(args.max_workers or len(tasks) or 1, len(tasks) or 1))

    def run_one(index: int, task: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        prompt = task.get("prompt", "")
        if not prompt:
            return index, {
                "provider": task.get("provider", ""),
                "role": task.get("role", ""),
                "agent_id": task.get("agent_id", ""),
                "text": "",
                "error": "Task prompt is required.",
                "duration_ms": 0,
                "session_id": None,
            }
        agent_id = task.get("agent_id") or f"task-{index + 1}"
        return index, _call(
            prompt,
            provider=task.get("provider", ""),
            role=task.get("role", ""),
            agent_id=agent_id,
            cwd=task.get("cwd", args.cwd),
            session=task.get("session", ""),
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_one, index, task) for index, task in enumerate(tasks)]
        for future in as_completed(futures):
            index, result = future.result()
            results[index] = result

    final = [result for result in results if result is not None]
    if args.json:
        payload = json.dumps({"results": final}, indent=2)
    else:
        parts = []
        for index, result in enumerate(final, start=1):
            status = "ERROR" if result["error"] else "OK"
            body = result["error"] or result["text"]
            parts.append(f"[{index}] {result['provider'].upper()} {status}\n{body}")
        payload = "\n\n---\n\n".join(parts)
    _write_output(payload, args.output)
    return 1 if any(result["error"] for result in final) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chorus",
        description="Run Chorus providers from the shell.",
        epilog=ROOT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser(
        "ask",
        help="Ask one provider or role",
        description="Ask one provider or role.",
        epilog=ASK_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ask_parser.add_argument("--provider", default="", help="Provider name: gemini, copilot, codex, or claude")
    ask_parser.add_argument("--role", default="", help="Configured role name; overrides --provider when set")
    ask_parser.add_argument("--agent-id", default="", help="Stable logical agent id for isolated sessions")
    ask_parser.add_argument("--session", default="", help="Resume a specific provider session id")
    ask_parser.add_argument("--cwd", default=".", help="Working directory for the provider call")
    ask_parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    ask_parser.add_argument("--output", default="", help="Write output to a file instead of stdout")
    ask_parser.add_argument("prompt", nargs=argparse.REMAINDER, help="Prompt text")
    ask_parser.set_defaults(func=cmd_ask)

    parallel_parser = subparsers.add_parser(
        "parallel",
        help="Run task JSON entries in parallel",
        description="Run task JSON entries in parallel.",
        epilog=PARALLEL_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parallel_parser.add_argument("--tasks", required=True, help="Path to task JSON list or object with a tasks list")
    parallel_parser.add_argument("--cwd", default=".", help="Default working directory for all tasks")
    parallel_parser.add_argument("--max-workers", type=int, default=0, help="Maximum concurrent provider calls")
    parallel_parser.add_argument("--json", action="store_true", help="Print structured JSON output")
    parallel_parser.add_argument("--output", default="", help="Write output to a file instead of stdout")
    parallel_parser.set_defaults(func=cmd_parallel)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
