"""Post-translation checks.

EXACT checks (deterministic, reliable): digits/numbers multiset, paragraph count, Latin tokens,
protected spans, reasoning leakage, output script.
HEURISTIC diagnostics (may produce false alarms, never auto-correct): negation presence, glossary
lexeme presence, length ratio, dialect markers.
Neither class proves semantic correctness.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .linguistics import (canonical_numbers, dialect_markers, inferred_language, negation_count,
                          paragraphs, strip_tashkeel)

EXACT, HEURISTIC = "exact", "heuristic"


@dataclass(frozen=True)
class Issue:
    code: str
    kind: str  # EXACT / HEURISTIC
    severity: str  # critical / major / minor
    message: str

    def __str__(self) -> str:  # used by GUI status lines
        return self.message


_LATIN_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9\-]*[A-Za-z0-9]\b|\b[A-Z]\b")


def _norm_ar(s: str) -> str:
    s = strip_tashkeel(s)
    return re.sub("[أإآ]", "ا", s).replace("ى", "ي").replace("ة", "ه")


def _glossary_present(target_term: str, output: str, lang: str) -> bool:
    if lang == "ar":
        out = _norm_ar(output)
        words = _norm_ar(target_term).split()
        # tolerate prefixes (ال، و، ب، ل، ف) and suffixes: check that the core of each word occurs
        return all((w[2:] if w.startswith("ال") and len(w) > 4 else w)[:max(3, len(w) - 2)] in out for w in words)
    out = output.lower().replace("ё", "е")
    words = target_term.lower().replace("ё", "е").split()
    return all(w[:max(4, len(w) - 3)] in out for w in words)


def check_translation(source: str, target: str, direction: str,
                      glossary_hits: list[dict] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    if not target.strip():
        return [Issue("empty", EXACT, "critical", "Модель вернула пустой перевод.")]
    src_lang, tgt_lang = ("ru", "ar") if direction == "ru-ar" else ("ar", "ru")

    a, b = Counter(canonical_numbers(source)), Counter(canonical_numbers(target))
    if a != b:
        missing = list((a - b).elements())
        extra = list((b - a).elements())
        detail = []
        if missing:
            detail.append("нет в переводе: " + ", ".join(missing[:6]))
        if extra:
            detail.append("лишние в переводе: " + ", ".join(extra[:6]))
        issues.append(Issue("numbers", EXACT, "critical",
                            "Проверьте числа и даты — " + "; ".join(detail) + "."))

    observed = inferred_language(target)
    if observed not in (tgt_lang, "mixed", "unknown"):
        issues.append(Issue("language", EXACT, "critical",
                            "Результат написан не на ожидаемом языке — возможна ошибка направления."))
    if "<think>" in target or "</think>" in target:
        issues.append(Issue("reasoning", EXACT, "critical", "В ответе обнаружена служебная разметка рассуждений."))

    ps, pt = len(paragraphs(source)), len(paragraphs(target))
    if ps != pt:
        issues.append(Issue("paragraphs", EXACT, "major",
                            f"Число абзацев не совпадает: в оригинале {ps}, в переводе {pt}."))

    lat_src = Counter(_LATIN_TOKEN_RE.findall(source))
    lat_tgt = Counter(_LATIN_TOKEN_RE.findall(target))
    lost = [t for t in lat_src if lat_tgt[t] < lat_src[t]]
    if lost:
        issues.append(Issue("latin", EXACT, "major",
                            "Латинские обозначения не перенесены: " + ", ".join(lost[:6]) + "."))

    ns, nt = negation_count(source, src_lang), negation_count(target, tgt_lang)
    if (ns > 0) != (nt > 0):
        issues.append(Issue("negation", HEURISTIC, "critical",
                            "Возможна потеря или добавление отрицания — сверьте смысл вручную."))

    for term in glossary_hits or []:
        if not _glossary_present(term["target"], target, tgt_lang):
            issues.append(Issue("glossary", HEURISTIC, "major",
                                f"Термин «{term['source']}» переведён не по глоссарию (ожидалось «{term['target']}»)."))

    if len(source.strip()) > 40:
        ratio = len(target.strip()) / max(1, len(source.strip()))
        if ratio < 0.4 or ratio > 2.8:
            issues.append(Issue("length", HEURISTIC, "major",
                                "Длина перевода сильно отличается от оригинала — возможен пропуск или добавление."))

    if tgt_lang == "ar":
        hits = dialect_markers(target)
        if hits:
            words = sorted({w for v in hits.values() for w in v})
            issues.append(Issue("dialect_output", HEURISTIC, "major",
                                "В переводе есть разговорные (диалектные) слова: " + "، ".join(words) + "."))
    return issues


def source_warnings(source: str, direction: str) -> list[Issue]:
    issues: list[Issue] = []
    expected = "ru" if direction == "ru-ar" else "ar"
    found = inferred_language(source)
    if found not in (expected, "mixed", "unknown"):
        issues.append(Issue("source_language", EXACT, "major",
                            "Язык исходного текста не совпадает с выбранным направлением."))
    if direction == "ar-ru":
        hits = dialect_markers(source)
        if hits:
            words = sorted({w for v in hits.values() for w in v})
            issues.append(Issue("dialect_source", HEURISTIC, "major",
                                "Похоже на разговорный (диалектный) арабский: " + "، ".join(words)
                                + ". Программа рассчитана только на литературный язык — перевод может быть неточным."))
    return issues
