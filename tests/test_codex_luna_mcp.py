import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from tools import codex_luna_mcp as bridge


def rpc(method, request_id=1, params=None):
    result = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        result["id"] = request_id
    if params is not None:
        result["params"] = params
    return json.dumps(result) + "\n"


class MCPBridgeTests(unittest.TestCase):
    def run_server(self, input_text):
        output = io.StringIO()
        server = bridge.CodexServer(io.StringIO(input_text), output)
        server.serve()
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_initialize_ping_and_tool_schema(self):
        messages = self.run_server(
            rpc("initialize", 1, {"protocolVersion": "2025-06-18"})
            + rpc("ping", 2)
            + rpc("tools/list", 3)
        )
        self.assertEqual(messages[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(messages[0]["result"]["capabilities"], {"tools": {}})
        self.assertEqual(messages[1]["result"], {})
        tool = messages[2]["result"]["tools"][0]
        self.assertEqual(tool["name"], "luna_task")
        self.assertEqual(tool["inputSchema"]["properties"]["mode"]["enum"], ["read-only", "workspace-write"])
        self.assertEqual(tool["inputSchema"]["properties"]["mode"]["default"], "read-only")

    def test_tool_uses_fixed_command_and_stdin_without_shell(self):
        class FakeProcess:
            pid = 456
            returncode = 0
            stderr = io.StringIO("")

            def poll(self):
                return self.returncode

            def communicate(self, text, timeout):
                self.input_text = text
                with open(args[args.index("-o") + 1], "w", encoding="utf-8") as output:
                    output.write("done")
                self.timeout = timeout
                return (None, "")

        process = FakeProcess()

        def popen(argv, **kwargs):
            nonlocal args
            args = argv
            process.popen_kwargs = kwargs
            return process

        args = []
        output = io.StringIO()
        server = bridge.CodexServer(io.StringIO(), output)
        with patch.object(bridge.subprocess, "Popen", side_effect=popen):
            server._tool_call("call-1", {"name": "luna_task", "arguments": {"task": "inspect this"}})
            server.pool.shutdown(wait=True)
        response = json.loads(output.getvalue())
        self.assertEqual(response["result"]["content"][0]["text"], "done")
        self.assertEqual(process.input_text, "inspect this")
        self.assertEqual(process.timeout, bridge.TIMEOUT)
        self.assertEqual(args[:8], [bridge.CODEX, "-a", "never", "exec", "-C", str(bridge.ROOT), "-m", bridge.MODEL])
        self.assertIn("read-only", args)
        self.assertIn("features.multi_agent=false", args)
        self.assertFalse(process.popen_kwargs.get("shell", False))
        self.assertTrue(process.popen_kwargs["start_new_session"])
        self.assertEqual(process.popen_kwargs["stdin"], bridge.subprocess.PIPE)

    def test_invalid_arguments_and_invalid_json_do_not_crash(self):
        messages = self.run_server(
            "{nope}\n" + rpc("tools/call", 2, {"name": "luna_task", "arguments": {"task": "  "}})
            + rpc("tools/call", 3, {"name": "luna_task", "arguments": {"task": "ok", "cwd": "/tmp"}})
        )
        self.assertEqual(messages[0]["error"]["code"], -32700)
        self.assertEqual(messages[1]["error"]["code"], -32602)
        self.assertEqual(messages[2]["error"]["code"], -32602)

    def test_failed_exit_returns_tool_error_with_limited_stderr(self):
        class BadProcess:
            pid = 457
            returncode = 9
            stderr = io.StringIO("x" * 5000)

            def poll(self):
                return self.returncode

            def communicate(self, text, timeout):
                return (None, self.stderr.getvalue())

        output = io.StringIO()
        server = bridge.CodexServer(io.StringIO(
            ""), output)
        with patch.object(bridge.subprocess, "Popen", return_value=BadProcess()):
            server._tool_call(5, {"name": "luna_task", "arguments": {"task": "fail"}})
            server.pool.shutdown(wait=True)
        response = json.loads(output.getvalue())
        self.assertTrue(response["result"]["isError"])
        error_text = response["result"]["content"][0]["text"]
        self.assertLess(len(error_text), 1300)
        self.assertIn("código 9", error_text)
        self.assertIn("x" * 100, error_text)

    def test_timeout_drains_stderr_and_empty_answer_is_error(self):
        class TimeoutProcess:
            pid = 459
            returncode = None
            stderr = io.StringIO()
            calls = 0

            def poll(self):
                return self.returncode

            def communicate(self, text=None, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise bridge.subprocess.TimeoutExpired("codex", timeout)
                return (None, "timeout diagnostic")

        timed_out = TimeoutProcess()
        output = io.StringIO()
        server = bridge.CodexServer(io.StringIO(), output)
        with patch.object(bridge.subprocess, "Popen", return_value=timed_out), patch.object(
            bridge.CodexServer, "_terminate", side_effect=lambda proc: setattr(proc, "returncode", -15)
        ):
            server._tool_call("timeout", {"name": "luna_task", "arguments": {"task": "wait"}})
            server.pool.shutdown(wait=True)
        timeout_error = json.loads(output.getvalue())["result"]["content"][0]["text"]
        self.assertIn("540s", timeout_error)
        self.assertIn("timeout diagnostic", timeout_error)
        self.assertEqual(timed_out.calls, 2)

        class EmptyProcess:
            pid = 460
            returncode = 0
            stderr = io.StringIO("")

            def poll(self):
                return self.returncode

            def communicate(self, text, timeout):
                return (None, "")

        output = io.StringIO()
        server = bridge.CodexServer(io.StringIO(), output)
        with patch.object(bridge.subprocess, "Popen", return_value=EmptyProcess()):
            server._tool_call("empty", {"name": "luna_task", "arguments": {"task": "empty"}})
            server.pool.shutdown(wait=True)
        empty_response = json.loads(output.getvalue())
        self.assertTrue(empty_response["result"]["isError"])
        self.assertIn("resposta final", empty_response["result"]["content"][0]["text"])

    def test_read_only_tasks_can_overlap_and_writes_are_serialized(self):
        guard = threading.Lock()
        read_active = 0
        max_read_active = 0
        write_active = 0
        max_write_active = 0
        reads_started = threading.Event()

        def fake_run(key, task, mode):
            nonlocal read_active, max_read_active, write_active, max_write_active
            with guard:
                if mode == "read-only":
                    read_active += 1
                    max_read_active = max(max_read_active, read_active)
                    if read_active >= 2:
                        reads_started.set()
                else:
                    write_active += 1
                    max_write_active = max(max_write_active, write_active)
            if mode == "read-only":
                reads_started.wait(1)
            time.sleep(0.02)
            with guard:
                if mode == "read-only":
                    read_active -= 1
                else:
                    write_active -= 1
            return {"content": [{"type": "text", "text": "ok"}]}

        server = bridge.CodexServer(io.StringIO(), io.StringIO())
        with patch.object(server, "_run_codex", side_effect=fake_run):
            for request_id, mode in (("r1", "read-only"), ("r2", "read-only"), ("w1", "workspace-write"), ("w2", "workspace-write")):
                server._tool_call(request_id, {"name": "luna_task", "arguments": {"task": request_id, "mode": mode}})
            server.pool.shutdown(wait=True)
        self.assertGreaterEqual(max_read_active, 2)
        self.assertEqual(max_write_active, 1)

    def test_cancellation_terminates_process_group(self):
        class RunningProcess:
            pid = 458
            returncode = None
            stderr = io.StringIO("")

            def poll(self):
                return self.returncode

            def communicate(self, text, timeout):
                self.started.set()
                self.cancel.wait(2)
                self.returncode = -15
                return (None, "")

            started = threading.Event()
            cancel = threading.Event()

        process = RunningProcess()
        output = io.StringIO()
        request = rpc("tools/call", "job", {"name": "luna_task", "arguments": {"task": "wait"}})

        class Input:
            def __iter__(self):
                yield request
                process.started.wait(1)
                yield rpc("notifications/cancelled", None, {"requestId": "job"})

        server = bridge.CodexServer(Input(), output)

        def terminate(proc):
            proc.cancel.set()
            proc.returncode = -15

        with patch.object(bridge.subprocess, "Popen", return_value=process), patch.object(
            bridge.CodexServer, "_terminate", side_effect=terminate
        ):
            server.serve()
            server.pool.shutdown(wait=True)
        response = json.loads(output.getvalue())
        self.assertTrue(response["result"]["isError"])
        self.assertIn("cancelada", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
