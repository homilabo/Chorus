import unittest
from unittest.mock import patch, MagicMock
from cli import CLIResult, call_copilot, clear_sessions, set_session, _run

class TestCLIRun(unittest.TestCase):
    def setUp(self):
        self.cmd = ["test-cli", "-p", "hello"]
        self.timeout = 300
        self.cwd = "/tmp"

    @patch("subprocess.run")
    def test_pure_json_result_and_session_id(self, mock_run):
        # 1. pure JSON stdout with {"result": "...", "session_id": "..."}
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"result": "hello world", "session_id": "sess_123"}',
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.session_id, "sess_123")
        self.assertEqual(result.error, "")
        self.assertIsInstance(result.duration_ms, int)

    @patch("subprocess.run")
    def test_json_response_field(self, mock_run):
        # 2. JSON stdout with {"response": "..."}
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"response": "im a response"}',
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.text, "im a response")

    @patch("subprocess.run")
    def test_jsonl_thread_start_and_assistant_text(self, mock_run):
        # 3. JSONL stdout containing thread/session start event and final assistant text event
        jsonl_output = (
            '{"type": "thread.started", "thread_id": "thread_abc"}\n'
            '{"type": "item.completed", "data": {"text": "final text"}}\n'
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=jsonl_output,
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.text, "final text")
        self.assertEqual(result.session_id, "thread_abc")

    @patch("subprocess.run")
    def test_jsonl_result_event_sets_session_id(self, mock_run):
        jsonl_output = (
            '{"type": "assistant.message", "data": {"content": "final text"}}\n'
            '{"type": "result", "sessionId": "session_abc", "exitCode": 0}\n'
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=jsonl_output,
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.text, "final text")
        self.assertEqual(result.session_id, "session_abc")

    @patch("subprocess.run")
    def test_jsonl_error_event(self, mock_run):
        # 4. JSONL error event should return CLIResult.error
        jsonl_output = (
            '{"type": "error", "message": "something went wrong"}\n'
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=jsonl_output,
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.error, "something went wrong")
        self.assertEqual(result.text, "")

    @patch("subprocess.run")
    def test_empty_stdout_returns_error(self, mock_run):
        # 5. empty stdout should return error="Empty response"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.error, "Empty response")

    @patch("subprocess.run")
    def test_nonzero_returncode_with_stderr_no_stdout(self, mock_run):
        # 6. nonzero returncode with stderr and no stdout should return stderr-truncated error
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="An extremely long error message that should be truncated because it exceeds the five hundred character limit set in the implementation of the _run function in cli.py" * 5
        )
        result = _run(self.cmd, self.timeout, self.cwd)
        self.assertEqual(result.text, "")
        self.assertTrue(len(result.error) <= 500)
        self.assertTrue(result.error.startswith("An extremely long error message"))

    @patch("cli.get_provider_config", return_value={"model": "gemma", "timeout": 300})
    @patch("cli._run", return_value=CLIResult(text="ok"))
    def test_call_copilot_resumes_specific_session_id(self, mock_run, _mock_config):
        clear_sessions()
        set_session("agent:copilot:worker-a", "session_abc")

        call_copilot("hello", cwd="/tmp", session_key="agent:copilot:worker-a")

        cmd = mock_run.call_args.args[0]
        self.assertIn("--resume=session_abc", cmd)
        self.assertNotIn("--continue", cmd)
        clear_sessions()

if __name__ == "__main__":
    unittest.main()
