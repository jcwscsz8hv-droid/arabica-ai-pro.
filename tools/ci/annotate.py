"""Emit file contents as GitHub Actions annotations (readable via the check-runs API).

Job logs and artifacts live on blob storage the dev sandbox cannot reach, while annotations are
served by api.github.com. Usage: python tools/ci/annotate.py TITLE FILE [FILE...]
"""
from __future__ import annotations

import sys
from pathlib import Path

CHUNK = 3800
MAX_ANNOTATIONS = 9


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main() -> int:
    title = sys.argv[1]
    text = ""
    for f in sys.argv[2:]:
        p = Path(f)
        if p.is_file():
            text += f"### {p.name}\n" + p.read_text(encoding="utf-8", errors="replace") + "\n"
        else:
            text += f"### {f}: missing\n"
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or ["(empty)"]
    if len(chunks) > MAX_ANNOTATIONS:
        chunks = chunks[:2] + ["…"] + chunks[-(MAX_ANNOTATIONS - 3):]
    for i, c in enumerate(chunks, 1):
        print(f"::notice title={title} {i}/{len(chunks)}::{esc(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
