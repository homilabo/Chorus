import unittest
from unittest.mock import patch

import chorus_server
from cli import CLIResult


class TestAgentSessions(unittest.IsolatedAsyncioTestCase):
    async def test_ask_agent_id_uses_isolated_provider_session_key(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text="ok", session_id="sess-1")

        with patch("chorus_server._get_provider_fn", return_value=fake_provider):
            response = await chorus_server.ask(
                "hello",
                provider="copilot",
                agent_id="worker-a",
                cwd="/tmp/project",
            )

        self.assertIn("ok", response)
        self.assertIn("[session: sess-1]", response)
        self.assertEqual(calls, [
            ("hello", {"cwd": "/tmp/project", "session_key": "agent:copilot:worker-a"})
        ])

    async def test_ask_without_agent_id_preserves_role_session_key(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text="ok")

        with (
            patch("chorus_server._resolve_role", return_value=("copilot", "model-x")),
            patch("chorus_server._get_provider_fn", return_value=fake_provider),
        ):
            await chorus_server.ask("hello", role="reviewer", cwd="/tmp/project")

        self.assertEqual(calls, [
            ("hello", {"model": "model-x", "cwd": "/tmp/project", "session_key": "reviewer"})
        ])

    async def test_parallel_ask_agent_ids_use_distinct_session_keys(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text=prompt, duration_ms=1)

        with patch("chorus_server._get_provider_fn", return_value=fake_provider):
            response = await chorus_server.parallel_ask([
                {"provider": "copilot", "agent_id": "worker-a", "prompt": "one"},
                {"provider": "copilot", "agent_id": "worker-b", "prompt": "two"},
            ], cwd="/tmp/project")

        self.assertIn("one", response)
        self.assertIn("two", response)
        self.assertEqual(
            {kwargs["session_key"] for _, kwargs in calls},
            {"agent:copilot:worker-a", "agent:copilot:worker-b"},
        )

    async def test_parallel_ask_without_agent_id_preserves_default_provider_session(self):
        calls = []

        def fake_provider(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return CLIResult(text=prompt)

        with patch("chorus_server._get_provider_fn", return_value=fake_provider):
            await chorus_server.parallel_ask([
                {"provider": "copilot", "prompt": "one"},
            ], cwd="/tmp/project")

        self.assertEqual(calls, [("one", {"cwd": "/tmp/project"})])


class TestManagedTask(unittest.IsolatedAsyncioTestCase):
    async def test_managed_task_runs_owner_plan_workers_and_owner_summary(self):
        ask_calls = []
        parallel_calls = []

        async def fake_ask(prompt, **kwargs):
            ask_calls.append((prompt, kwargs))
            if len(ask_calls) == 1:
                return (
                    '{"tasks":['
                    '{"agent_id":"worker-a","role":"coder","prompt":"write tests"},'
                    '{"agent_id":"worker-b","role":"reviewer","prompt":"review edge cases"}'
                    ']}'
                )
            return "final summary"

        async def fake_parallel_ask(tasks, cwd="."):
            parallel_calls.append((tasks, cwd))
            return "worker output"

        with (
            patch("chorus_server.ask", side_effect=fake_ask),
            patch("chorus_server.parallel_ask", side_effect=fake_parallel_ask),
        ):
            result = await chorus_server.managed_task(
                "add session isolation",
                owner_role="reasoner",
                default_worker_role="coder",
                max_workers=3,
                cwd="/tmp/project",
                agent_prefix="task-1",
            )

        self.assertEqual(len(ask_calls), 2)
        self.assertEqual(ask_calls[0][1]["agent_id"], "task-1-owner")
        self.assertEqual(ask_calls[1][1]["agent_id"], "task-1-owner")
        self.assertEqual(parallel_calls, [([
            {"role": "coder", "agent_id": "worker-a", "prompt": "write tests"},
            {"role": "reviewer", "agent_id": "worker-b", "prompt": "review edge cases"},
        ], "/tmp/project")])
        self.assertIn("## Owner Plan", result)
        self.assertIn("worker output", result)
        self.assertIn("final summary", result)

    async def test_managed_task_falls_back_to_single_worker_when_plan_is_not_json(self):
        async def fake_ask(prompt, **kwargs):
            if "Decide whether workers are needed" in prompt:
                return "no structured plan"
            return "final summary"

        parallel_calls = []

        async def fake_parallel_ask(tasks, cwd="."):
            parallel_calls.append((tasks, cwd))
            return "worker output"

        with (
            patch("chorus_server.ask", side_effect=fake_ask),
            patch("chorus_server.parallel_ask", side_effect=fake_parallel_ask),
        ):
            await chorus_server.managed_task(
                "do the thing",
                default_worker_role="coder",
                cwd="/tmp/project",
                agent_prefix="task-2",
            )

        self.assertEqual(parallel_calls, [([
            {"role": "coder", "agent_id": "task-2-worker-1", "prompt": "do the thing"},
        ], "/tmp/project")])

    async def test_managed_task_preserves_owner_provider_choice_and_max_workers(self):
        async def fake_ask(prompt, **kwargs):
            if "Decide whether workers are needed" in prompt:
                return (
                    '{"tasks":['
                    '{"agent_id":"worker-a","provider":"copilot","prompt":"first"},'
                    '{"agent_id":"worker-b","role":"reviewer","prompt":"second"},'
                    '{"agent_id":"worker-c","role":"coder","prompt":"third"}'
                    ']}'
                )
            return "final summary"

        parallel_calls = []

        async def fake_parallel_ask(tasks, cwd="."):
            parallel_calls.append((tasks, cwd))
            return "worker output"

        with (
            patch("chorus_server.ask", side_effect=fake_ask),
            patch("chorus_server.parallel_ask", side_effect=fake_parallel_ask),
        ):
            await chorus_server.managed_task(
                "owner picks workers",
                max_workers=2,
                cwd="/tmp/project",
                agent_prefix="task-3",
            )

        self.assertEqual(parallel_calls, [([
            {"provider": "copilot", "agent_id": "worker-a", "prompt": "first"},
            {"role": "reviewer", "agent_id": "worker-b", "prompt": "second"},
        ], "/tmp/project")])

    async def test_managed_task_falls_back_when_owner_picks_unknown_role(self):
        async def fake_ask(prompt, **kwargs):
            if "Decide whether workers are needed" in prompt:
                return (
                    '{"tasks":['
                    '{"agent_id":"worker-a","role":"general-purpose","prompt":"inspect"}'
                    ']}'
                )
            return "final summary"

        parallel_calls = []

        async def fake_parallel_ask(tasks, cwd="."):
            parallel_calls.append((tasks, cwd))
            return "worker output"

        with (
            patch("chorus_server.ask", side_effect=fake_ask),
            patch("chorus_server.parallel_ask", side_effect=fake_parallel_ask),
        ):
            await chorus_server.managed_task(
                "owner picks unknown role",
                default_worker_role="coder",
                cwd="/tmp/project",
                agent_prefix="task-4",
            )

        self.assertEqual(parallel_calls, [([
            {"role": "coder", "agent_id": "worker-a", "prompt": "inspect"},
        ], "/tmp/project")])


if __name__ == "__main__":
    unittest.main()
