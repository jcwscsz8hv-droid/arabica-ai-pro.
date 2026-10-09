"""Protected spans: fragments that must reach the output byte-for-byte.

URLs, e-mails, file paths, code-like tokens and user-marked spans {{...}} are replaced by
placeholders ⟦1⟧, ⟦2⟧ … before translation and restored afterwards. Restoration is exact;
a missing or duplicated placeholder is reported (never silently dropped).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS = [
    re.compile(r"\{\{(.+?)\}\}"),                              # user-marked: {{keep me}}
    re.compile(r"https?://[^\s<>«»\"']+[^\s<>«»\"'.,;:!?)\]]"),  # URLs
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),            # e-mails
    re.compile(r"\b[A-Za-z]:\\[^\s]+"),                        # Windows paths
    re.compile(r"`[^`\n]+`"),                                  # inline code
]
PLACEHOLDER_RE = re.compile(r"⟦(\d+)⟧")


@dataclass
class Protected:
    text: str
    spans: dict[str, str] = field(default_factory=dict)


def protect(text: str) -> Protected:
    spans: dict[str, str] = {}
    counter = [0]

    def repl(m: re.Match) -> str:
        counter[0] += 1
        key = f"⟦{counter[0]}⟧"
        # user-marked spans keep their inner text, without braces
        spans[key] = m.group(1) if m.re is _PATTERNS[0] else m.group(0)
        return key

    out = text
    for pat in _PATTERNS:
        out = pat.sub(repl, out)
    return Protected(out, spans)


def restore(translated: str, prot: Protected) -> tuple[str, list[str]]:
    problems: list[str] = []
    found = PLACEHOLDER_RE.findall(translated)
    for key, value in prot.spans.items():
        n = translated.count(key)
        if n == 0:
            problems.append(f"Защищённый фрагмент «{value}» пропал из перевода — добавлен в конец.")
            translated = translated.rstrip() + " " + value
        elif n > 1:
            problems.append(f"Защищённый фрагмент «{value}» повторён в переводе {n} раз.")
        translated = translated.replace(key, value)
    unknown = [k for k in found if f"⟦{k}⟧" not in prot.spans]
    if unknown:
        problems.append("В переводе появились лишние служебные метки — проверьте фрагмент.")
        translated = PLACEHOLDER_RE.sub("", translated)
    return translated, problems
