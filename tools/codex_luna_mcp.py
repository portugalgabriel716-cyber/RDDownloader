#!/usr/bin/env python3
"""Small stdio MCP bridge that delegates tasks to the local Codex CLI."""

from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def find_codex() -> str:
    """Use the newest installed CLI, including the VS Code extension's CLI."""
    on_path = shutil.which("codex")
    candidates = [on_path] if on_path else []
    extensions = Path.home() / ".vscode" / "extensions"
    candidates.extend(str(p) for p in extensions.glob("openai.chatgpt-*/bin/*/codex")
                      if p.is_file() and os.access(p, os.X_OK))
    best = on_path or "codex"
    best_version = (0, 0, 0)
    for candidate in dict.fromkeys(candidates):
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True,
                                    text=True, timeout=3, check=True)
            match = re.search(r"codex-cli (\d+)\.(\d+)\.(\d+)", result.stdout)
            if match:
                version = tuple(int(part) for part in match.groups())
                if version > best_version:
                    best, best_version = candidate, version
        except (OSError, subprocess.SubprocessError):
            continue
    return best


CODEX = find_codex()
MODEL = "gpt-6-luna"
TIMEOUT = 540
SUPPORTED_PROTOCOLS = {
    "2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"
}
DEVELOPER_INSTRUCTIONS = (
    "Você é um executor delegado. Não reorquestre nem delegue tarefas. "
    "Trabalhe somente nos arquivos definidos na tarefa. Responda brevemente "
    "com resultado e evidências. Não leia segredos/credenciais, não publique e não faça commit."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "minLength": 1, "description": "Tarefa para o executor Codex."},
        "mode": {
            "type": "string",
            "enum": ["read-only", "workspace-write"],
            "default": "read-only",
            "description": "Permissão de sandbox para a tarefa.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class CodexServer:
    def __init__(self, input_stream=None, output_stream=None):
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="codex-luna")
        self.write_lock = threading.Lock()
        self.process_lock = threading.Lock()
        self.workspace_write_lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.cancelled: set[str] = set()
        self.stopping = threading.Event()
        self.shutdown_lock = threading.Lock()
        self.shutdown_complete = False

    @staticmethod
    def _key(request_id: Any) -> str:
        return json.dumps(request_id, sort_keys=True, ensure_ascii=False)

    def _send(self, message: dict[str, Any]) -> None:
        with self.write_lock:
            self.output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # The leader can exit while a child still owns an output pipe.
            # Kill the remaining group so communicate() can always drain it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _cancel(self, request_id: Any) -> None:
        key = self._key(request_id)
        with self.process_lock:
            self.cancelled.add(key)
            process = self.processes.get(key)
        if process:
            self._terminate(process)

    def _execute(self, request_id: Any, task: str, mode: str) -> dict[str, Any]:
        key = self._key(request_id)
        lock = self.workspace_write_lock if mode == "workspace-write" else _NullLock()
        try:
            with lock:
                if self.stopping.is_set() or key in self.cancelled:
                    raise RuntimeError("Tarefa cancelada")
                return self._run_codex(key, task, mode)
        except Exception as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        finally:
            with self.process_lock:
                self.processes.pop(key, None)
                self.cancelled.discard(key)

    def _run_codex(self, key: str, task: str, mode: str) -> dict[str, Any]:
        sandbox = "read-only" if mode == "read-only" else "workspace-write"
        with tempfile.TemporaryDirectory(prefix="codex-luna-") as temp_dir:
            answer_path = Path(temp_dir, "answer.txt")
            argv = [
                CODEX, "-a", "never", "exec", "-C", str(ROOT), "-m", MODEL,
                "-s", sandbox, "-c", 'model_reasoning_effort="medium"',
                "-c", "features.multi_agent=false", "-c",
                "developer_instructions=" + json.dumps(DEVELOPER_INSTRUCTIONS, ensure_ascii=False),
                "--ephemeral", "--color", "never", "-o", str(answer_path), "-",
            ]
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                start_new_session=True,
            )
            with self.process_lock:
                self.processes[key] = process
                already_cancelled = self.stopping.is_set() or key in self.cancelled
            if already_cancelled:
                self._terminate(process)
                raise RuntimeError("Tarefa cancelada")
            try:
                _, stderr = process.communicate(task, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                self._terminate(process)
                try:
                    _, stderr = process.communicate()
                except Exception:
                    stderr = ""
                stderr = stderr or ""
                raise RuntimeError(f"Codex excedeu o limite de {TIMEOUT}s: {stderr[-1200:]}")
            with self.process_lock:
                cancelled = key in self.cancelled or self.stopping.is_set()
            if cancelled:
                raise RuntimeError("Tarefa cancelada")
            if process.returncode != 0:
                stderr = stderr or ""
                raise RuntimeError(f"Codex terminou com código {process.returncode}: {stderr[-1200:]}")
            try:
                answer = answer_path.read_text(encoding="utf-8").strip()
            except OSError:
                answer = ""
            if not answer:
                raise RuntimeError("Codex não retornou resposta final.")
            return {"content": [{"type": "text", "text": answer}]}

    def _tool_call(self, request_id: Any, params: Any) -> None:
        if not isinstance(params, dict) or params.get("name") != "luna_task":
            self._error(request_id, -32602, "Ferramenta inválida")
            return
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            self._error(request_id, -32602, "arguments deve ser um objeto")
            return
        if set(args) - {"task", "mode"}:
            self._error(request_id, -32602, "arguments contém propriedades não suportadas")
            return
        task = args.get("task")
        mode = args.get("mode", "read-only")
        if not isinstance(task, str) or not task.strip() or mode not in ("read-only", "workspace-write"):
            self._error(request_id, -32602, "task deve ser texto não vazio e mode deve ser read-only ou workspace-write")
            return
        self.pool.submit(self._finish_tool, request_id, task.strip(), mode)

    def _finish_tool(self, request_id: Any, task: str, mode: str) -> None:
        result = self._execute(request_id, task, mode)
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _handle(self, message: Any) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            self._error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
            return
        method = message["method"]
        request_id = message.get("id")
        has_id = "id" in message
        params = message.get("params", {})
        if method == "notifications/cancelled":
            if isinstance(params, dict) and "requestId" in params:
                self._cancel(params["requestId"])
            return
        if method == "notifications/initialized":
            return
        if method == "initialize":
            protocol = params.get("protocolVersion") if isinstance(params, dict) else None
            if protocol not in SUPPORTED_PROTOCOLS:
                self._error(request_id, -32602, "Versão de protocolo MCP não suportada")
            else:
                self._send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "codex-luna", "version": "1.0.0"},
                }})
            return
        if method == "ping":
            if has_id:
                self._send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            return
        if method == "tools/list":
            if has_id:
                self._send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{
                    "name": "luna_task", "description": "Delegar uma tarefa para o Codex com modelo Luna 6.", "inputSchema": TOOL_SCHEMA,
                }]}})
            return
        if method == "tools/call":
            if has_id:
                self._tool_call(request_id, params)
            return
        if has_id:
            self._error(request_id, -32601, "Method not found")

    def serve(self) -> None:
        try:
            for line in self.input:
                if self.stopping.is_set():
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    self._error(None, -32700, "Parse error")
                    continue
                self._handle(message)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self.shutdown_lock:
            if self.shutdown_complete:
                return
            self.shutdown_complete = True
            self.stopping.set()
            with self.process_lock:
                processes = list(self.processes.values())
            for process in processes:
                self._terminate(process)
            self.pool.shutdown(wait=False, cancel_futures=True)


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def main() -> None:
    server = CodexServer()

    def stop_on_signal(signum, frame):
        server.shutdown()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGINT, stop_on_signal)
    server.serve()


if __name__ == "__main__":
    main()
