"""Persistent local inference backend (llama.cpp ``llama-server``) — loopback only.

Design (see docs/ADR/0002-inference-backend.md):
* One long-lived child process holds the GGUF weights in memory; requests never reload the model.
* The server is bound to 127.0.0.1 on a random free port and requires a random per-session API key,
  so other local programs cannot use it and nothing listens on external interfaces.
* HTTP is spoken with ``http.client`` directly — environment proxies are never consulted.
* Requests stream tokens; cancel or a stall closes the connection, which stops generation.
* On Windows the child is placed in a Job Object with KILL_ON_JOB_CLOSE, so it dies with the app.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

LOOPBACK = "127.0.0.1"
# Windows NTSTATUS for illegal instruction (CPU lacks a required ISA extension).
STATUS_ILLEGAL_INSTRUCTION = 0xC000001D


class BackendError(RuntimeError):
    """User-presentable error (Russian text)."""


class Cancelled(BackendError):
    pass


@dataclass(frozen=True)
class EngineConfig:
    binary: Path
    model: Path
    threads: int = 0  # 0 = auto (physical cores)
    context_tokens: int = 8192
    max_tokens: int = 2048
    start_timeout_s: int = 900
    stall_timeout_s: int = 180
    request_timeout_s: int = 3600
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def check_files(config: EngineConfig) -> None:
    if not config.binary.is_file():
        raise BackendError(f"Не найден локальный движок перевода: {config.binary}. Переустановите программу.")
    if not config.model.is_file():
        raise BackendError(f"Не найден файл модели: {config.model}. Переустановите программу.")
    with config.model.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise BackendError("Файл модели повреждён или не является GGUF. Переустановите программу.")
    if config.threads < 0 or config.context_tokens < 512 or config.max_tokens < 1:
        raise BackendError("Некорректные параметры модели.")


_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


def clean_generated_text(output: str) -> str:
    """Remove a closed reasoning block; refuse to show an unterminated one."""
    if "<think>" in output and "</think>" not in output:
        raise BackendError("Модель вернула незавершённый блок рассуждений. Перевод не отображён.")
    output = _THINK_RE.sub("", output)
    return output.replace("\x00", "").strip()


def physical_cores() -> int:
    try:
        import psutil  # bundled in the release

        n = psutil.cpu_count(logical=False)
        if n:
            return max(1, n)
    except Exception:  # noqa: BLE001
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


class _WinJob:
    """Kill-on-close Job Object so the server never outlives the GUI (Windows only)."""

    def __init__(self) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = h
            self._k32 = k32

    def assign(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]


def explain_exit(code: int | None, log_tail: str) -> str:
    """Translate a failed start into plain Russian."""
    if code is not None and (code & 0xFFFFFFFF) == STATUS_ILLEGAL_INSTRUCTION:
        return ("Процессор этого компьютера не поддерживает набор инструкций, нужный движку. "
                "Сообщите разработчику модель процессора (msinfo32 → «Процессор»).")
    low = log_tail.lower()
    if any(s in low for s in ("failed to allocate", "out of memory", "bad_alloc", "unable to allocate")):
        return "Недостаточно оперативной памяти для загрузки модели. Закройте другие программы и повторите."
    if "unknown model architecture" in low:
        return "Версия движка не поддерживает архитектуру модели (ошибка сборки дистрибутива)."
    if "failed to load model" in low or "invalid magic" in low:
        return "Не удалось загрузить модель: файл повреждён или несовместим. Переустановите программу."
    return f"Локальный движок завершился с кодом {code}. Подробности в журнале: {log_tail[-400:]}"


class LlamaServerEngine:
    """Persistent llama-server child process with streaming chat completions."""

    def __init__(self, config: EngineConfig, log_path: Path | None = None):
        self.config = config
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.key = ""
        self._lock = threading.Lock()
        self._job = None
        self._log_file = None
        self.load_seconds: float | None = None

    # ---------------- lifecycle ----------------
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _log_tail(self, n: int = 4000) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            return self.log_path.read_bytes()[-n:].decode("utf-8", errors="replace")
        except OSError:
            return ""

    def start(self, cancel: threading.Event | None = None,
              status: Callable[[str], None] | None = None) -> None:
        with self._lock:
            if self.running:
                return
            check_files(self.config)
            self.port = _free_port()
            self.key = secrets.token_urlsafe(24)
            threads = self.config.threads or physical_cores()
            args = [str(self.config.binary), "-m", str(self.config.model),
                    "--host", LOOPBACK, "--port", str(self.port), "--api-key", self.key,
                    "-c", str(self.config.context_tokens), "-t", str(threads), "-np", "1",
                    "--jinja", *self.config.extra_args]
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().endswith("_PROXY") and not k.upper().startswith("LLAMA_ARG_")}
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_file = self.log_path.open("wb")
            out = self._log_file if self._log_file else subprocess.DEVNULL
            try:
                self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out,
                                             stderr=subprocess.STDOUT, creationflags=flags,
                                             cwd=str(self.config.binary.parent), env=env)
            except OSError as ex:
                raise BackendError(f"Не удалось запустить локальный движок: {ex}") from ex
            self._job = _WinJob()
            self._job.assign(self.proc)
            t0 = time.monotonic()
            if status:
                status("Модель загружается в память…")
            while True:
                if cancel and cancel.is_set():
                    self.stop()
                    raise Cancelled("Загрузка модели отменена.")
                code = self.proc.poll()
                if code is not None:
                    tail = self._log_tail()
                    self.stop()
                    raise BackendError(explain_exit(code, tail))
                if time.monotonic() - t0 > self.config.start_timeout_s:
                    self.stop()
                    raise BackendError("Модель не загрузилась за отведённое время.")
                if self._health():
                    self.load_seconds = round(time.monotonic() - t0, 1)
                    return
                time.sleep(0.5)

    def stop(self) -> None:
        p = self.proc
        self.proc = None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(10)
        if self._log_file:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(LOOPBACK, self.port, timeout=timeout)

    def _health(self) -> bool:
        try:
            c = self._conn(3)
            c.request("GET", "/health")
            r = c.getresponse()
            r.read()
            c.close()
            return r.status == 200
        except OSError:
            return False

    # ---------------- inference ----------------
    def chat(self, messages: list[dict], *, max_tokens: int | None = None,
             temperature: float = 0.0, json_schema: dict | None = None,
             cancel: threading.Event | None = None,
             on_text: Callable[[str], None] | None = None) -> str:
        """Stream one chat completion with thinking disabled. Returns cleaned text."""
        if not self.running:
            self.start(cancel)
        body: dict = {
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "top_k": 1 if temperature == 0 else 20,
            "top_p": 1.0 if temperature == 0 else 0.8,
            "max_tokens": max_tokens or self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": True,
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": json_schema}}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        conn = self._conn(self.config.stall_timeout_s)
        pieces: list[str] = []
        finish = None
        t0 = time.monotonic()
        try:
            conn.request("POST", "/v1/chat/completions", body=payload, headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.key}", "Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                detail = resp.read()[:600].decode("utf-8", "replace")
                raise BackendError(f"Локальный движок отклонил запрос ({resp.status}): {detail}")
            for event in _iter_sse(resp):
                if cancel and cancel.is_set():
                    raise Cancelled("Перевод отменён пользователем.")
                if time.monotonic() - t0 > self.config.request_timeout_s:
                    raise BackendError("Превышено время ожидания перевода.")
                if event == "[DONE]":
                    break
                try:
                    obj = json.loads(event)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:
                    raise BackendError(f"Ошибка движка: {obj['error']}")
                for ch in obj.get("choices", []):
                    txt = (ch.get("delta") or {}).get("content")
                    if txt:
                        pieces.append(txt)
                        if on_text:
                            on_text(txt)
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        except socket.timeout as ex:
            raise BackendError("Модель перестала отвечать (нет новых токенов). Попробуйте ещё раз.") from ex
        except (ConnectionError, http.client.HTTPException) as ex:
            if not self.running:
                raise BackendError(explain_exit(None, self._log_tail())) from ex
            raise BackendError(f"Связь с локальным движком прервана: {ex}") from ex
        finally:
            conn.close()
        text = clean_generated_text("".join(pieces))
        if finish == "length":
            raise BackendError("Перевод обрезан: фрагмент слишком длинный для одного прохода.")
        if not text:
            raise BackendError("Модель вернула пустой ответ.")
        return text


def _iter_sse(resp) -> Iterable[str]:
    buf = b""
    while True:
        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                yield line[5:].strip().decode("utf-8", errors="replace")
