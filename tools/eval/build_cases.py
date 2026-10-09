"""Compile tools/eval/src/*.txt into docs/EVAL_CASES.jsonl and validate the set.

Usage: python tools/eval/build_cases.py [--check]
Each case gets deterministic checks derived from the SOURCE (numbers, negation presence) plus
optional `must=` items. The script also cross-checks every reference against its own source with
the same deterministic checks and prints mismatches: they are either reference errors or expected
lexical-negation cases (which must carry a note).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from arabica.linguistics import canonical_numbers, is_short_input, negation_count  # noqa: E402

SRC = ROOT / "tools" / "eval" / "src"
OUT = ROOT / "docs" / "EVAL_CASES.jsonl"
CATS = {"GEN": ("general", 50), "POL": ("official_political", 30), "DIP": ("diplomatic", 20),
        "MIL": ("military", 20), "SCI": ("science_tech", 20), "LIT": ("literary", 20),
        "AMB": ("polysemy_idioms", 20), "NND": ("negation_numbers_dates_names", 20)}
LEVELS = {"1": "basic", "2": "intermediate", "3": "advanced"}


def parse(path: Path, direction: str) -> list[dict]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            raise SystemExit(f"{path.name}:{n}: malformed line")
        cat, level, source, reference = parts[:4]
        extra = {}
        for p in parts[4:]:
            k, _, v = p.partition("=")
            extra[k.strip()] = v.strip()
        if cat not in CATS or level not in LEVELS:
            raise SystemExit(f"{path.name}:{n}: bad category/level")
        out.append({"cat": cat, "level": int(level), "source": source.strip(), "reference": reference.strip(),
                    "must": [m for m in extra.get("must", "").split(";") if m], "note": extra.get("note", ""),
                    "where": f"{path.name}:{n}", "direction": direction})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only, do not write")
    a = ap.parse_args()
    rows = []
    for f in sorted(SRC.glob("ru_ar_*.txt")):
        rows += parse(f, "ru-ar")
    for f in sorted(SRC.glob("ar_ru_*.txt")):
        rows += parse(f, "ar-ru")
    problems, warnings = [], []
    cases = []
    counters: dict[str, Counter] = {"ru-ar": Counter(), "ar-ru": Counter()}
    seen = set()
    for r in rows:
        d = r["direction"]
        counters[d][r["cat"]] += 1
        key = (d, r["source"])
        if key in seen:
            problems.append(f"duplicate source {r['where']}")
        seen.add(key)
        src_lang, tgt_lang = ("ru", "ar") if d == "ru-ar" else ("ar", "ru")
        alts = [x.strip() for x in r["reference"].split(" / ")] if " / " in r["reference"] else []
        nums = canonical_numbers(r["source"])
        neg = negation_count(r["source"], src_lang) > 0
        # self-check of the reference
        if Counter(nums) != Counter(canonical_numbers(r["reference"])):
            problems.append(f"numbers differ between source and reference {r['where']}: "
                            f"{nums} vs {canonical_numbers(r['reference'])}")
        if neg != (negation_count(r["reference"], tgt_lang) > 0) and not r["note"]:
            warnings.append(f"negation presence differs (no note) {r['where']}")
        for m in r["must"]:
            if m.lower() not in r["reference"].lower():
                problems.append(f"must-item '{m}' not in reference {r['where']}")
        idx = counters[d][r["cat"]]
        cid = f"{'RA' if d == 'ru-ar' else 'AR'}-{r['cat']}-{idx:03d}"
        cases.append({
            "id": cid, "direction": d, "category": CATS[r["cat"]][0], "level": r["level"],
            "level_name": LEVELS[str(r["level"])], "source": r["source"], "reference": r["reference"],
            "acceptable_alternatives": alts,
            "checks": {"numbers": nums, "negation_in_source": neg, "must_contain": r["must"],
                       "short_input": is_short_input(r["source"])},
            "note": r["note"], "reference_origin": "authored-by-claude-2026-10-09",
            "review_status": "pending_expert_review", "requires_professional_translator": True,
            "license": "CC0-1.0", "split": "closed_test_do_not_train",
            "source_sha1": hashlib.sha1(r["source"].encode("utf-8")).hexdigest(),
        })
    for d, c in counters.items():
        for cat, (_, need) in CATS.items():
            if c[cat] != need:
                problems.append(f"{d}: category {cat} has {c[cat]} cases, expected {need}")
        lv = Counter(x["level"] for x in cases if x["direction"] == d)
        print(f"{d}: {sum(c.values())} cases; levels {dict(sorted(lv.items()))}")
    for w in warnings:
        print("WARN", w)
    for p in problems:
        print("ERROR", p)
    if problems:
        return 1
    if not a.check:
        OUT.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
        print(f"written {len(cases)} cases -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
