"""Text normalisation, script detection, segmentation and lightweight heuristics.

Nothing here is a linguistic oracle: these are deterministic helpers whose results are either
exact (digit normalisation, segmentation that preserves text verbatim) or explicitly heuristic
(dialect markers, negation counts) and only produce warnings.
"""
from __future__ import annotations

import re
import unicodedata

ARABIC_RE = re.compile(r"[ء-يٱ-ۓ]")
RUSSIAN_RE = re.compile(r"[А-Яа-яЁё]")
LATIN_RE = re.compile(r"[A-Za-z]")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# Arabic decimal separator U+066B, thousands separator U+066C.
NUMBERS_RE = re.compile(r"(?<![\w.])\d+(?:[.,٫]\d+)*(?![\w])")
TASHKEEL_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
TATWEEL = "ـ"


def inferred_language(text: str) -> str:
    ar = len(ARABIC_RE.findall(text))
    ru = len(RUSSIAN_RE.findall(text))
    if not ar and not ru:
        return "unknown"
    if ar and ru and min(ar, ru) / max(ar, ru) > 0.32:
        return "mixed"
    return "ar" if ar >= ru else "ru"


def normalize_digits(text: str) -> str:
    return unicodedata.normalize("NFKC", text.translate(ARABIC_DIGITS))


def strip_tashkeel(text: str) -> str:
    return TASHKEEL_RE.sub("", text).replace(TATWEEL, "")


def canonical_number(token: str) -> str:
    """'2,5' / '2.5' / '2٫5' -> '2.5'; '1 000' handled by caller. Thousand groups '1,000,000' -> '1000000'."""
    t = token.replace("٫", ".").replace("٬", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}", t) or re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", t):
        return re.sub(r"[.,]", "", t)
    return t.replace(",", ".")


def extract_numbers(text: str) -> list[str]:
    """Digit sequences after digit normalisation; joins '1 000 000' style groups."""
    norm = normalize_digits(text)
    norm = re.sub(r"(?<=\d)[   ](?=\d{3}(?!\d))", "", norm)
    return NUMBERS_RE.findall(norm)


def canonical_numbers(text: str) -> list[str]:
    return [canonical_number(n) for n in extract_numbers(text)]


# ---------------------------------------------------------------- dialect markers (heuristic)
# Frequent colloquial-only function words; presence => warn, never block or silently normalise.
DIALECT_MARKERS = {
    "egyptian": ["مش", "عايز", "عاوز", "إزاي", "ازاي", "دلوقتي", "كده", "بتاع", "إمبارح", "النهارده", "ليه"],
    "levantine": ["بدي", "بدك", "هيك", "كتير", "شو", "هلق", "هلأ", "منيح", "ليش", "مبارح"],
    "gulf": ["شلون", "وايد", "ابي", "أبي", "يبي", "الحين", "چذي", "زين"],
    "maghrebi": ["بزاف", "واش", "كيفاش", "دابا", "مزيان", "بغيت"],
    "iraqi": ["شكو", "ماكو", "اكو", "هواية", "شنو"],
}
_WORD_RE = re.compile(r"[ء-ي]+")


def dialect_markers(text: str) -> dict[str, list[str]]:
    words = set(_WORD_RE.findall(strip_tashkeel(text)))
    hits = {}
    for dialect, markers in DIALECT_MARKERS.items():
        found = sorted(m for m in markers if m in words)
        if found:
            hits[dialect] = found
    return hits


# ---------------------------------------------------------------- negation (heuristic counts)
# Sentential negators only. Lexical negation (без, не- prefixes, غير، عدم، بدون) is deliberately excluded:
# it maps across languages unpredictably (незаконный ↔ غير قانوني) and would cause false alarms.
RU_NEG_RE = re.compile(r"(?<![А-Яа-яЁё])(не|нет|ни|нельзя|никогда|никто|ничто|ничего|нигде|никак|ниоткуда)(?![А-Яа-яЁё])",
                       re.IGNORECASE)
AR_NEG_WORDS = {"لا", "لم", "لن", "ليس", "ليست", "ليسوا", "لست", "لسنا", "لستم", "ليسا", "ولا", "ولم", "ولن",
                "وليس", "وليست", "فلا", "فلم", "فلن", "فليس", "أبدا", "قط"}


# Fixed expressions that contain a negator but are not negations ("still", "especially", "not only").
_RU_NOT_NEG_RE = re.compile(r"(?<![А-Яа-яЁё])(не только|не раз|"
                            r"не столько|ни разу не)(?![А-Яа-яЁё])", re.IGNORECASE)
_AR_NOT_NEG_RE = re.compile(r"(?:^|\s)[وف]?(?:لا|ما)\s+(?:يزال|تزال|يزالون|زال|زالت|سيما|بد)(?=\s|$)")


def negation_count(text: str, lang: str) -> int:
    if lang == "ru":
        return len(RU_NEG_RE.findall(_RU_NOT_NEG_RE.sub(" ", text)))
    words = _WORD_RE.findall(_AR_NOT_NEG_RE.sub(" ", strip_tashkeel(text)))
    return sum(1 for w in words if w in AR_NEG_WORDS)


# ---------------------------------------------------------------- segmentation
def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def is_short_input(text: str) -> bool:
    """A word or a short phrase without sentence punctuation."""
    t = text.strip()
    return 0 < word_count(t) <= 3 and "\n" not in t and not re.search(r"[.!?؟;؛]$", t)


def segment(text: str, max_chars: int = 2200) -> list[str]:
    """Split on paragraph and sentence boundaries, preserving all source text verbatim."""
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")
    if not text.strip():
        return []
    parts = re.split(r"(\n\s*\n)", text)
    out: list[str] = []
    buf = ""
    for piece in parts:
        if len(buf) + len(piece) <= max_chars:
            buf += piece
            continue
        if buf:
            out.append(buf)
            buf = ""
        fragments = re.split(r"(?<=[.!?؟…])(?=\s)", piece)
        for fragment in fragments:
            while len(fragment) > max_chars:
                pivot = fragment.rfind(" ", max_chars // 2, max_chars + 1)
                pivot = max_chars if pivot < 0 else pivot + 1
                out.append(fragment[:pivot])
                fragment = fragment[pivot:]
            if len(buf) + len(fragment) > max_chars and buf:
                out.append(buf)
                buf = ""
            buf += fragment
    if buf:
        out.append(buf)
    if "".join(out) != text:  # pragma: no cover - defensive invariant
        raise AssertionError("Segmentation must preserve the source verbatim")
    return out
