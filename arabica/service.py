"""Translation orchestration: segmentation with context, glossary, protected spans, checks, history."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .backend import BackendError, Cancelled
from .linguistics import is_short_input, segment, strip_tashkeel
from .prompts import WORDCARD_SCHEMA, build_messages, diacritize_messages, wordcard_messages
from .protect import protect, restore
from .quality import EXACT, Issue, check_translation, source_warnings
from .translit import transcribe

MAX_CHARS = 100_000
SEGMENT_CHARS = 1500


class Engine(Protocol):
    def chat(self, messages: list[dict], *, max_tokens: int | None = None, temperature: float = 0.0,
             json_schema: dict | None = None, cancel: threading.Event | None = None,
             on_text: Callable[[str], None] | None = None) -> str: ...


@dataclass
class Segment:
    source: str
    target: str = ""
    issues: list[Issue] = field(default_factory=list)
    leading: str = ""
    trailing: str = ""


@dataclass
class Translation:
    text: str
    warnings: list[Issue]
    chunks: int
    segments: list[Segment] = field(default_factory=list)
    glossary_used: list[dict] = field(default_factory=list)


class Translator:
    def __init__(self, engine: Engine, store=None, model_name: str = ""):
        self.engine = engine
        self.store = store
        self.model_name = model_name

    # ------------------------------------------------------------ translation
    def translate(self, source: str, direction: str, mode: str = "accurate",
                  cancel: threading.Event | None = None,
                  progress: Callable[[int, int], None] | None = None,
                  user_note: str = "", domain: str | None = None,
                  on_segment: Callable[[int, Segment], None] | None = None) -> Translation:
        if not source.strip():
            raise ValueError("Введите текст для перевода.")
        if len(source) > MAX_CHARS:
            raise ValueError(f"Текст слишком длинный для одного перевода (максимум {MAX_CHARS:,} символов). "
                             "Разделите документ на части.".replace(",", " "))
        if direction not in ("ru-ar", "ar-ru"):
            raise ValueError("Неизвестное направление перевода.")
        warnings = source_warnings(source, direction)
        glossary_hits = self.store.terms_for(source, direction, domain) if self.store else []
        chunks = segment(source, SEGMENT_CHARS)
        short = len(chunks) == 1 and is_short_input(source)
        segments: list[Segment] = []
        context: list[tuple[str, str]] = []
        for i, chunk in enumerate(chunks):
            if cancel and cancel.is_set():
                raise Cancelled("Перевод отменён пользователем.")
            seg = Segment(source=chunk.strip(),
                          leading=chunk[:len(chunk) - len(chunk.lstrip())],
                          trailing=chunk[len(chunk.rstrip()):])
            if seg.source:
                self._translate_segment(seg, direction, mode, glossary_hits, context, user_note, short, cancel)
                context.append((seg.source, seg.target))
            segments.append(seg)
            if on_segment:
                on_segment(i, seg)
            if progress:
                progress(i + 1, len(chunks))
        final = self.join(segments)
        multi = len(segments) > 1
        for i, s in enumerate(segments):
            for iss in s.issues:
                msg = f"Фрагмент {i + 1}: {iss.message}" if multi else iss.message
                warnings.append(Issue(iss.code, iss.kind, iss.severity, msg))
        if self.store:
            self.store.log(source, final, direction, mode, self.model_name)
        return Translation(final, warnings, len(chunks), segments, glossary_hits)

    def _translate_segment(self, seg: Segment, direction: str, mode: str, glossary_all: list[dict],
                           context: list[tuple[str, str]], user_note: str, short: bool,
                           cancel: threading.Event | None) -> None:
        prot = protect(seg.source)
        hits = [t for t in glossary_all if self.store and self._occurs(t, seg.source, direction)]
        msgs = build_messages(prot.text, direction, mode, hits, context, user_note, short)
        out = self.engine.chat(msgs, cancel=cancel)
        out, problems = restore(out, prot)
        seg.target = out.strip()
        seg.issues = [Issue("protected", EXACT, "major", p) for p in problems]
        seg.issues += check_translation(seg.source, seg.target, direction, hits) if not short else \
            [i for i in check_translation(seg.source, seg.target, direction, hits) if i.code != "length"]

    @staticmethod
    def _occurs(term: dict, text: str, direction: str) -> bool:
        from .glossary import normalize, term_occurs
        lang = direction[:2]
        return term_occurs(normalize(term["source"], lang), normalize(text, lang), lang)

    def retranslate_segment(self, result: Translation, index: int, direction: str, mode: str,
                            cancel: threading.Event | None = None, user_note: str = "") -> Translation:
        seg = result.segments[index]
        context = [(s.source, s.target) for s in result.segments[max(0, index - 2):index] if s.source]
        self._translate_segment(seg, direction, mode, result.glossary_used, context, user_note, False, cancel)
        result.text = self.join(result.segments)
        return result

    @staticmethod
    def join(segments: list[Segment]) -> str:
        return "".join(s.leading + s.target + s.trailing for s in segments)

    # ------------------------------------------------------------ lexical tools
    def word_card(self, word: str, direction: str, context: str = "",
                  cancel: threading.Event | None = None) -> dict:
        if not word.strip() or len(word.split()) > 4:
            raise ValueError("Для словарной карточки введите одно слово или устойчивое выражение (до 4 слов).")
        raw = self.engine.chat(wordcard_messages(word, direction, context), json_schema=WORDCARD_SCHEMA,
                               max_tokens=1200, cancel=cancel)
        try:
            card = json.loads(raw)
        except json.JSONDecodeError as ex:
            raise BackendError("Модель вернула некорректную словарную карточку. Попробуйте ещё раз.") from ex
        card["headword"] = word.strip()
        card["unverified"] = True  # generated by the model, not from a reference dictionary
        if direction == "ar-ru" and card.get("vocalized"):
            if strip_tashkeel(card["vocalized"]).replace("ٱ", "ا") != strip_tashkeel(word.strip()).replace("ٱ", "ا"):
                card["ambiguity_note"] = (card.get("ambiguity_note", "") +
                                          " Огласовка модели не совпала по буквам со словом — не используйте её.").strip()
                card["vocalized"] = ""
            else:
                card["transcription"] = transcribe(card["vocalized"]).text
        return card

    def diacritize(self, arabic: str, cancel: threading.Event | None = None) -> dict:
        """Vocalise MSA text; the letter skeleton is verified to be unchanged (exact check)."""
        if not arabic.strip():
            raise ValueError("Нет арабского текста для огласовки.")
        out = self.engine.chat(diacritize_messages(arabic), cancel=cancel,
                               max_tokens=max(256, len(arabic) * 3))
        same = _skeleton(out) == _skeleton(arabic)
        tr = transcribe(out) if same else None
        notes = []
        if not same:
            notes.append("Модель изменила буквы текста при огласовке — результат отклонён.")
        elif len(arabic.split()) <= 2:
            notes.append("Без контекста слово может читаться по-разному; огласовка показывает наиболее частое чтение.")
        if tr and not tr.complete:
            notes.append("Огласовка неполная — транскрипция приблизительная.")
        return {"vocalized": out if same else "", "transcription": tr.text if tr else "",
                "letters_preserved": same, "notes": notes}


def _skeleton(s: str) -> str:
    s = strip_tashkeel(s).replace("ٱ", "ا")
    return "".join(s.split())
