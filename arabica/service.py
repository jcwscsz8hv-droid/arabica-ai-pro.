"""Translation orchestration, glossary, progress and quality warnings."""
from __future__ import annotations
from dataclasses import dataclass
import threading
from typing import Callable, Protocol
from .backend import Cancelled
from .linguistics import segment, inferred_language
from .prompts import make_prompt
from .quality import check_translation

class Engine(Protocol):
    def generate(self, prompt: str, cancel: threading.Event | None = None) -> str: ...

@dataclass
class Translation:
    text: str
    warnings: list[str]
    chunks: int


class Translator:
    def __init__(self, engine: Engine, store=None):
        self.engine = engine
        self.store = store

    def translate(self, source: str, direction: str, mode: str = "accurate",
                  cancel: threading.Event | None = None,
                  progress: Callable[[int,int], None] | None = None) -> Translation:
        if not source.strip():
            raise ValueError("Введите текст для перевода.")
        if len(source) > 100_000:
            raise ValueError("Документ слишком длинный для текущей версии (макс. 100 000 символов).")
        if direction not in ("ru-ar", "ar-ru"):
            raise ValueError("Неизвестное направление перевода.")
        found = inferred_language(source)
        expected = "ru" if direction == "ru-ar" else "ar"
        warnings = []
        if found not in (expected, "mixed", "unknown"):
            warnings.append("Язык исходного текста не совпадает с выбранным направлением.")
        glossary = self.store.terms(direction) if self.store else []
        chunks = segment(source)
        translated = []
        prev_context = ""
        for i, chunk in enumerate(chunks):
            if cancel and cancel.is_set():
                raise Cancelled("Перевод отменён пользователем.")
            if not chunk.strip():
                translated.append(chunk)
                if progress:
                    progress(i + 1, len(chunks))
                continue
            # Avoid irreversible stripping when source contains paragraph whitespace.
            leading = chunk[:len(chunk) - len(chunk.lstrip())]
            trailing = chunk[len(chunk.rstrip()):]
            content = chunk.strip()
            prompt = make_prompt(content, direction, mode, glossary, prev_context)
            result = self.engine.generate(prompt, cancel=cancel).strip()
            translated.append(leading + result + trailing)
            warnings += [f"Фрагмент {i+1}: {w}" for w in check_translation(content, result, direction)]
            prev_context = result[-600:]
            if progress:
                progress(i + 1, len(chunks))
        final = "".join(translated)
        if self.store:
            self.store.log(source, final, direction, mode)
        return Translation(final, warnings, len(chunks))
