"""Text normalization and lightweight language checks (not a dialect classifier)."""
from __future__ import annotations
import re
import unicodedata

ARABIC_RE = re.compile(r"[\u0621-\u064A\u0671-\u06D3]")
RUSSIAN_RE = re.compile(r"[А-Яа-яЁё]")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
NUMBERS_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")


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


def extract_numbers(text: str) -> list[str]:
    """Conservative: simple decimal numbers. Does not claim numerical equivalence."""
    return NUMBERS_RE.findall(normalize_digits(text))


def segment(text: str, max_chars: int = 2200) -> list[str]:
    """Split on paragraph and sentence boundaries, preserving all source text."""
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")
    if not text.strip():
        return []
    # Keep separators so segments can be rejoined without altering source.
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
        # Split oversized paragraphs on sentence endings; then hard-wrap as last resort.
        fragments = re.split(r"(?<=[.!?؟。])(?=\s)", piece)
        for fragment in fragments:
            while len(fragment) > max_chars:
                # Prefer whitespace near end of chunk.
                pivot = fragment.rfind(" ", max_chars // 2, max_chars + 1)
                if pivot < 0:
                    pivot = max_chars
                else:
                    pivot += 1
                out.append(fragment[:pivot])
                fragment = fragment[pivot:]
            if len(buf) + len(fragment) > max_chars and buf:
                out.append(buf)
                buf = ""
            buf += fragment
    if buf:
        out.append(buf)
    assert "".join(out) == text, "Segmentation must preserve the source verbatim"
    return out
