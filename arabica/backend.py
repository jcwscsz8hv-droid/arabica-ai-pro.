"""No cloud: llama-cli is a local child process and reads a local GGUF."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import tempfile
import threading
import time


class BackendError(RuntimeError):
    pass


class Cancelled(BackendError):
    pass


@dataclass(frozen=True)
class EngineConfig:
    binary: Path
    model: Path
    threads: int = 6
    context_tokens: int = 8192
    max_tokens: int = 2048
    timeout_seconds: int = 1200


def check_files(config: EngineConfig) -> None:
    if not config.binary.is_file():
        raise BackendError(f"Не найден локальный движок: {config.binary}")
    if not config.model.is_file():
        raise BackendError(f"Не найдена локальная GGUF-модель: {config.model}")
    with config.model.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise BackendError("Файл модели повреждён или не является GGUF.")
    if config.threads < 1 or config.context_tokens < 512 or config.max_tokens < 1:
        raise BackendError("Некорректные параметры модели.")


def clean_generated_text(output: str) -> str:
    # Strictly reject unfinished hidden reasoning rather than displaying it.
    if "<think>" in output and "</think>" not in output:
        raise BackendError("Модель вернула незавершённый блок рассуждений. Перевод не отображён.")
    output = re.sub(r"<think>.*?</think>", "", output, flags=re.DOTALL)
    return output.strip().replace("\x00", "")


class LlamaCliEngine:
    """Initial compatibility adapter. Reloads model for each request.

    Claude should replace with persistent native session after verifying proper
    Qwen chat templates; do not call this production-optimized.
    """
    def __init__(self, config: EngineConfig):
        self.config = config

    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str:
        check_files(self.config)
        if cancel and cancel.is_set():
            raise Cancelled("Перевод отменён.")
        temp_path = None
        try:
            fd, filename = tempfile.mkstemp(prefix="arabica_prompt_", suffix=".txt")
            temp_path = Path(filename)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(prompt)
            args = [str(self.config.binary), "--offline", "--model", str(self.config.model),
                "--file", str(temp_path), "--single-turn", "--no-display-prompt",
                "--no-show-timings", "--threads", str(self.config.threads),
                "--ctx-size", str(self.config.context_tokens), "--n-predict", str(self.config.max_tokens),
                "--gpu-layers", "0", "--temp", "0.15"]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=flags)
            except OSError as ex:
                raise BackendError(f"Не удалось запустить локальный движок: {ex}") from ex
            started = time.monotonic()
            try:
                while True:
                    if cancel and cancel.is_set():
                        process.kill()
                        process.communicate()
                        raise Cancelled("Перевод отменён пользователем.")
                    if time.monotonic() - started > self.config.timeout_seconds:
                        process.kill()
                        process.communicate()
                        raise BackendError("Время ожидания модели истекло.")
                    try:
                        stdout, stderr = process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            if process.returncode:
                raise BackendError("Ошибка llama.cpp (код %s): %s" %
                    (process.returncode, err[-1000:] or out[-1000:]))
            result = clean_generated_text(out)
            if not result:
                raise BackendError("Модель вернула пустой ответ.")
            return result
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
