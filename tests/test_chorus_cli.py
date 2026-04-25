import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import chorus_cli
from cli import CLIResult


class TestChorusCli(unittest.TestCase):
    def test_help_includes_examples(self):
        parser = chorus_cli.build_parser()
        help_text = parser.format_help()
        self.assertIn("Examples:", help_text)
        self.assertIn("chorus parallel --tasks /tmp/tasks.json", help_text)

        subparsers_action = next(
            action for action in parser._actions
            if isinstance(action, chorus_cli.argparse._SubParsersAction)
        )
        ask_help = subparsers_action.choices["ask"].format_help()
        parallel_help = subparsers_action.choices["parallel"].format_help()

        self.assertIn("chorus ask --provider copilot", ask_help)
        self.assertIn("Task fields:", parallel_help)

    def test_ask_calls_provider_with_agent_session_key(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text="ok", session_id="sess-1", duration_ms=3)

        stdout = StringIO()
        with patch.dict(chorus_cli.AVAILABLE_PROVIDERS, {"copilot": fake_provider}):
            with redirect_stdout(stdout):
                code = chorus_cli.main([
                    "ask",
                    "--provider", "copilot",
                    "--agent-id", "worker-a",
                    "--cwd", "/tmp/project",
                    "--json",
                    "hello",
                ])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["text"], "ok")
        self.assertEqual(calls, [
            ("hello", {"cwd": "/tmp/project", "session_key": "agent:copilot:worker-a"})
        ])

    def test_ask_writes_json_output_file(self):
        def fake_provider(prompt, **kwargs):
            return CLIResult(text="ok")

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "out.json"
            with patch.dict(chorus_cli.AVAILABLE_PROVIDERS, {"copilot": fake_provider}):
                code = chorus_cli.main([
                    "ask",
                    "--provider", "copilot",
                    "--json",
                    "--output", str(output),
                    "hello",
                ])

            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(data["provider"], "copilot")
        self.assertEqual(data["text"], "ok")

    def test_parallel_runs_tasks_with_isolated_default_agent_ids(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text=prompt, duration_ms=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = Path(tmpdir) / "tasks.json"
            output = Path(tmpdir) / "results.json"
            tasks.write_text(json.dumps([
                {"provider": "copilot", "prompt": "one"},
                {"provider": "copilot", "prompt": "two"},
            ]), encoding="utf-8")

            with patch.dict(chorus_cli.AVAILABLE_PROVIDERS, {"copilot": fake_provider}):
                code = chorus_cli.main([
                    "parallel",
                    "--tasks", str(tasks),
                    "--cwd", "/tmp/project",
                    "--json",
                    "--output", str(output),
                ])

            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual([item["text"] for item in data["results"]], ["one", "two"])
        self.assertEqual(
            {kwargs["session_key"] for _, kwargs in calls},
            {"agent:copilot:task-1", "agent:copilot:task-2"},
        )

    def test_parallel_returns_error_for_missing_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = Path(tmpdir) / "tasks.json"
            output = Path(tmpdir) / "results.json"
            tasks.write_text(json.dumps([{"provider": "copilot"}]), encoding="utf-8")

            code = chorus_cli.main([
                "parallel",
                "--tasks", str(tasks),
                "--json",
                "--output", str(output),
            ])
            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(data["results"][0]["error"], "Task prompt is required.")


if __name__ == "__main__":
    unittest.main()
