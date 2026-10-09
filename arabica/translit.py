"""Deterministic Cyrillic transcription of VOCALISED Modern Standard Arabic.

Simplified practical Russian transcription: consonants by a fixed table, short vowels from tashkeel,
long vowels from ا/و/ي after the matching short vowel, shadda doubles the consonant, tanwin -> ан/ин/ун,
the article assimilates before sun letters (аш-шамс) and is «аль-» otherwise, ta marbuta -> «а» in pause
and «т» + vowel when vocalised.

The result is only as good as the vocalisation. Consonants without any vowel/sukun information set
`complete=False`, and the UI marks the transcription as approximate (LINGUISTIC_CONTRACT.md §8).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FATHA, DAMMA, KASRA, SUKUN, SHADDA = "َ", "ُ", "ِ", "ْ", "ّ"
FATHATAN, DAMMATAN, KASRATAN = "ً", "ٌ", "ٍ"
SUPERSCRIPT_ALIF = "ٰ"
MARKS = {FATHA, DAMMA, KASRA, SUKUN, SHADDA, FATHATAN, DAMMATAN, KASRATAN, SUPERSCRIPT_ALIF}
SHORT = {FATHA: "а", DAMMA: "у", KASRA: "и"}
TANWIN = {FATHATAN: "ан", DAMMATAN: "ун", KASRATAN: "ин"}

CONS = {
    "ب": "б", "ت": "т", "ث": "с", "ج": "дж", "ح": "х", "خ": "х", "د": "д", "ذ": "з", "ر": "р",
    "ز": "з", "س": "с", "ش": "ш", "ص": "с", "ض": "д", "ط": "т", "ظ": "з", "ع": "‘", "غ": "г",
    "ف": "ф", "ق": "к", "ك": "к", "ل": "л", "م": "м", "ن": "н", "ه": "х", "و": "в", "ي": "й",
    "ء": "’", "أ": "’", "إ": "’", "ؤ": "’", "ئ": "’",
}
SUN = set("تثدذرزسشصضطظلن")
LETTER_RE = re.compile(r"[ء-يٱ]")


@dataclass
class Transcription:
    text: str
    complete: bool


def _units(word: str) -> list[tuple[str, set[str]]]:
    units: list[tuple[str, set[str]]] = []
    for ch in word:
        if ch in MARKS and units:
            units[-1][1].add(ch)
        elif ch != "ـ":
            units.append((ch, set()))
    return units


def _vowel_of(marks: set[str]) -> str:
    for m in (FATHATAN, DAMMATAN, KASRATAN):
        if m in marks:
            return TANWIN[m]
    for m in (FATHA, DAMMA, KASRA):
        if m in marks:
            return SHORT[m]
    if SUPERSCRIPT_ALIF in marks:
        return "а"
    return ""


def _word(word: str) -> tuple[str, bool]:
    u = _units(word)
    out: list[str] = []
    complete = True
    i = 0
    if len(u) > 2 and u[0][0] in "اٱ" and u[1][0] == "ل":
        nxt = u[2][0]
        out.append(("а" + CONS[nxt] + "-") if nxt in SUN else "аль-")
        i = 2
    elif u and u[0][0] in "اٱ":
        v = _vowel_of(u[0][1])
        out.append(v or "и")  # hamzat al-wasl at the start of an utterance
        i = 1
    prev = None  # last short vowel letter written
    article_end = i if i == 2 and out and out[0].endswith("-") and out[0] != "аль-" else -1
    while i < len(u):
        ch, marks = u[i]
        last = i == len(u) - 1
        v = _vowel_of(marks)
        if ch in "اى" and not v:
            if prev != "а" and not (out and out[-1].endswith("ан")):
                out.append("а")
            prev = "а"
            i += 1
            continue
        if ch == "آ":
            out.append("’а" if out else "а")
            prev = "а"
            i += 1
            continue
        if ch == "و" and prev == "у" and not v and SHADDA not in marks:
            prev = "у"
            i += 1
            continue
        if ch == "ي" and prev == "и" and not v and SHADDA not in marks:
            prev = "и"
            i += 1
            continue
        if ch == "ة":
            out.append(("т" + v) if v else ("" if out and out[-1].endswith("а") else "а"))
            prev = "а"
            i += 1
            continue
        c = CONS.get(ch)
        if c is None:
            out.append(ch)
            prev = None
            i += 1
            continue
        if ch in "أإؤئء" and not out:
            c = ""
            if not v:
                v = "и" if ch == "إ" else "а"
        if ch == "و" and not v and SUKUN in marks and prev == "а":
            out.append("у")  # diphthong aw: يَوْم -> яум
            prev = None
            i += 1
            continue
        if SHADDA in marks and c not in ("‘", "’", "") and i != article_end:
            c = c + c if len(c) == 1 else c[:-1] + c  # дж -> ддж
        if not v and SUKUN not in marks and not last:
            complete = False
        if c.endswith("й") and v.startswith("а"):
            seg = c[:-1] + "я" + v[1:]
        elif c.endswith("й") and v.startswith("у"):
            seg = c[:-1] + "ю" + v[1:]
        elif c == "л" and not v:
            seg = "ль"
        else:
            seg = c + v
        out.append(seg)
        prev = v[:1] if v else None
        i += 1
    return "".join(out), complete


def transcribe(text: str) -> Transcription:
    parts = re.split(r"(\s+|[،؛؟.,!?:;«»\"()\-])", text)
    res: list[str] = []
    complete = True
    for p in parts:
        if p and LETTER_RE.search(p):
            w, ok = _word(p)
            res.append(w)
            complete = complete and ok
        else:
            res.append({"،": ",", "؛": ";", "؟": "?"}.get(p, p))
    out = "".join(res)
    out = re.sub(r"(^|[.!?]\s+)([а-яё])", lambda m: m.group(1) + m.group(2).upper(), out)
    return Transcription(out, complete)
