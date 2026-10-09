"""Local SQLite store: terminology glossary (with versions and provenance), history, favourites.

No remote services. Schema: arabica/data/glossary_schema.sql (= docs/GLOSSARY_SCHEMA.sql).
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .linguistics import strip_tashkeel

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("data") / "glossary_schema.sql"
DOMAINS = ("general", "political", "diplomatic", "military", "legal", "economic", "science", "technical",
           "literary")
DOMAIN_RU = {"general": "общая", "political": "политика", "diplomatic": "дипломатия", "military": "военное дело",
             "legal": "право", "economic": "экономика", "science": "наука", "technical": "техника",
             "literary": "литература"}
_AR_PREFIX = r"(?:و|ف|ب|ل|ك|وب|ول|فب|فل)?(?:ال|لل)?"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str, lang: str) -> str:
    t = " ".join(text.split())
    if lang == "ar":
        t = strip_tashkeel(t)
        t = re.sub("[أإآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه")
        return t
    return t.lower().replace("ё", "е")


def _ru_pattern(norm: str) -> re.Pattern:
    parts = []
    for w in norm.split():
        stem = w if len(w) <= 4 else w[: max(4, len(w) - 2)]
        parts.append(re.escape(stem) + r"[а-яё]*")
    return re.compile(r"(?<![а-яё])" + r"\s+".join(parts) + r"(?![а-яё])")


def _ar_pattern(norm: str) -> re.Pattern:
    parts = []
    for w in norm.split():
        core = w[2:] if w.startswith("ال") and len(w) > 3 else w
        parts.append(_AR_PREFIX + re.escape(core) + r"[ء-ي]{0,3}")
    return re.compile(r"(?<![ء-ي])" + r"\s+".join(parts) + r"(?![ء-ي])")


def term_occurs(term_norm: str, text_norm: str, lang: str) -> bool:
    pat = _ar_pattern(term_norm) if lang == "ar" else _ru_pattern(term_norm)
    return bool(pat.search(text_norm))


class LocalStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            self._migrate_legacy(conn)
            if not conn.execute("SELECT 1 FROM schema_version").fetchone():
                conn.execute("INSERT INTO schema_version VALUES (?,?)", (SCHEMA_VERSION, now()))
            conn.execute("INSERT OR IGNORE INTO source_ref(code,title,kind) VALUES ('USER','Добавлено пользователем','user')")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _migrate_legacy(conn) -> None:
        """v0.2 scaffold had a flat `glossary` table; move its rows into term/equivalent."""
        if not conn.execute("SELECT name FROM sqlite_master WHERE name='glossary' AND type='table'").fetchone():
            return
        for r in conn.execute("SELECT source,target,direction,domain,notes FROM glossary").fetchall():
            lang = "ru" if r["direction"] == "ru-ar" else "ar"
            dom = r["domain"] if r["domain"] in DOMAINS else "general"
            cur = conn.execute("""INSERT OR IGNORE INTO term(lang,source,source_norm,domain,origin,created_at,updated_at)
                               VALUES (?,?,?,?, 'import', ?, ?)""",
                               (lang, r["source"], normalize(r["source"], lang), dom, now(), now()))
            if cur.lastrowid:
                conn.execute("INSERT OR IGNORE INTO equivalent(term_id,target,kind,note) VALUES (?,?, 'preferred', ?)",
                             (cur.lastrowid, r["target"], r["notes"] or ""))
        conn.execute("DROP TABLE glossary")

    # ------------------------------------------------------------ terms
    def add_term(self, entry: dict, reason: str = "") -> int:
        """Insert or update a term. entry: lang, source, domain, preferred, acceptable[], forbidden[],
        notes{}, pos, gender, number, grammar_note, priority, status, origin, examples[], sources[]."""
        lang = entry.get("lang")
        source = (entry.get("source") or "").strip()
        preferred = (entry.get("preferred") or "").strip()
        domain = entry.get("domain") or "general"
        if lang not in ("ru", "ar") or not source or not preferred:
            raise ValueError("Необходимо заполнить язык, термин и основной перевод")
        if domain not in DOMAINS:
            raise ValueError(f"Неизвестная область: {domain}")
        norm = normalize(source, lang)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM term WHERE lang=? AND source_norm=? AND domain=?",
                               (lang, norm, domain)).fetchone()
            fields = dict(pos=entry.get("pos", ""), gender=entry.get("gender", ""), number=entry.get("number", ""),
                          grammar_note=entry.get("grammar_note", ""), priority=int(entry.get("priority", 50)),
                          bidirectional=int(entry.get("bidirectional", 1)), status=entry.get("status", "draft"),
                          origin=entry.get("origin", "user"))
            if row:
                snap = self._snapshot(conn, row["id"])
                conn.execute("INSERT INTO term_history(term_id,version,changed_at,snapshot,reason) VALUES (?,?,?,?,?)",
                             (row["id"], row["version"], now(), json.dumps(snap, ensure_ascii=False), reason))
                conn.execute(f"""UPDATE term SET source=?, {', '.join(k + '=?' for k in fields)},
                                 version=version+1, updated_at=? WHERE id=?""",
                             (source, *fields.values(), now(), row["id"]))
                tid = row["id"]
                conn.execute("DELETE FROM equivalent WHERE term_id=?", (tid,))
                conn.execute("DELETE FROM term_example WHERE term_id=?", (tid,))
            else:
                cur = conn.execute(f"""INSERT INTO term(lang,source,source_norm,domain,{', '.join(fields)},
                                       created_at,updated_at) VALUES (?,?,?,?,{', '.join('?' * len(fields))},?,?)""",
                                   (lang, source, norm, domain, *fields.values(), now(), now()))
                tid = cur.lastrowid
            notes = entry.get("notes") or {}
            conn.execute("INSERT INTO equivalent(term_id,target,kind,note) VALUES (?,?, 'preferred', ?)",
                         (tid, preferred, notes.get(preferred, "")))
            for kind in ("acceptable", "forbidden"):
                for t in entry.get(kind, []) or []:
                    if t.strip() and t.strip() != preferred:
                        conn.execute("INSERT OR IGNORE INTO equivalent(term_id,target,kind,note) VALUES (?,?,?,?)",
                                     (tid, t.strip(), kind, notes.get(t, "")))
            for ex in entry.get("examples", []) or []:
                conn.execute("INSERT INTO term_example(term_id,source_text,target_text) VALUES (?,?,?)",
                             (tid, ex["source"], ex["target"]))
            for s in entry.get("sources", []) or []:
                conn.execute("""INSERT OR IGNORE INTO source_ref(code,title,kind,publisher,year,licence,note)
                                VALUES (?,?,?,?,?,?,?)""",
                             (s["code"], s.get("title", s["code"]), s.get("kind", "proposal"), s.get("publisher", ""),
                              str(s.get("year", "")), s.get("licence", ""), s.get("note", "")))
                sid = conn.execute("SELECT id FROM source_ref WHERE code=?", (s["code"],)).fetchone()["id"]
                conn.execute("INSERT OR REPLACE INTO term_source(term_id,source_ref,locator) VALUES (?,?,?)",
                             (tid, sid, s.get("locator", "")))
            return tid

    def put_term(self, source: str, target: str, direction: str, domain: str = "general", notes: str = "") -> None:
        """Simple user entry from the GUI."""
        if direction not in ("ru-ar", "ar-ru") or not source.strip() or not target.strip():
            raise ValueError("Необходимо заполнить термин, перевод и направление")
        self.add_term({"lang": direction[:2], "source": source, "preferred": target, "domain": domain,
                       "notes": {target.strip(): notes} if notes else {}, "origin": "user",
                       "status": "approved", "priority": 80, "sources": [{"code": "USER", "kind": "user"}]})

    def delete_term(self, term_id: int) -> None:
        with self._connect() as conn:
            snap = self._snapshot(conn, term_id)
            conn.execute("INSERT INTO term_history(term_id,version,changed_at,snapshot,reason) VALUES (?,?,?,?,?)",
                         (term_id, snap.get("version", 0), now(), json.dumps(snap, ensure_ascii=False), "deleted"))
            conn.execute("DELETE FROM term WHERE id=?", (term_id,))

    @staticmethod
    def _snapshot(conn, term_id: int) -> dict:
        row = conn.execute("SELECT * FROM term WHERE id=?", (term_id,)).fetchone()
        if not row:
            return {}
        d = dict(row)
        d["equivalents"] = [dict(r) for r in conn.execute(
            "SELECT target,kind,note FROM equivalent WHERE term_id=?", (term_id,))]
        return d

    def _rows(self, conn, lang: str) -> list[dict]:
        rows = conn.execute("""SELECT t.*, e.target AS preferred FROM term t JOIN equivalent e
                               ON e.term_id=t.id AND e.kind='preferred'
                               WHERE t.lang=? AND t.status!='deprecated'""", (lang,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            eq = conn.execute("SELECT target,kind,note FROM equivalent WHERE term_id=?", (r["id"],)).fetchall()
            d["acceptable"] = [e["target"] for e in eq if e["kind"] == "acceptable"]
            d["forbidden"] = [e["target"] for e in eq if e["kind"] == "forbidden"]
            d["note"] = next((e["note"] for e in eq if e["kind"] == "preferred"), "")
            out.append(d)
        return out

    def terms(self, direction: str, limit: int = 2000) -> list[dict]:
        """All usable entries for a direction as {source, target, domain, note, forbidden, priority, id}."""
        src = direction[:2]
        tgt = "ar" if src == "ru" else "ru"
        with self._connect() as conn:
            direct = self._rows(conn, src)
            reverse = [r for r in self._rows(conn, tgt) if r["bidirectional"]]
        out = [{"id": r["id"], "source": r["source"], "target": r["preferred"], "domain": r["domain"],
                "note": r["note"], "forbidden": r["forbidden"], "priority": r["priority"], "status": r["status"],
                "acceptable": r["acceptable"]} for r in direct]
        out += [{"id": r["id"], "source": r["preferred"], "target": r["source"], "domain": r["domain"],
                 "note": r["note"], "forbidden": [], "priority": r["priority"] - 1, "status": r["status"],
                 "acceptable": [], "reverse": True} for r in reverse]
        out.sort(key=lambda d: (-d["priority"], d["source"]))
        return out[:limit]

    def terms_for(self, text: str, direction: str, domain: str | None = None, limit: int = 40) -> list[dict]:
        """Entries whose source term occurs in `text` (inflection-tolerant). Domain-specific first."""
        lang = direction[:2]
        tnorm = normalize(text, lang)
        hits = []
        seen = set()
        for t in self.terms(direction):
            if t["source"] in seen:
                continue
            if term_occurs(normalize(t["source"], lang), tnorm, lang):
                hits.append(t)
                seen.add(t["source"])
        hits.sort(key=lambda d: (d["domain"] != domain, -d["priority"]))
        return hits[:limit]

    def conflicts(self, direction: str) -> list[tuple[str, list[str]]]:
        """Same source term with different preferred targets in different domains (shown to the user)."""
        by: dict[str, set[str]] = {}
        for t in self.terms(direction):
            if not t.get("reverse"):
                by.setdefault(t["source"], set()).add(t["target"])
        return [(s, sorted(v)) for s, v in by.items() if len(v) > 1]

    def export_json(self, path: Path | str) -> int:
        with self._connect() as conn:
            out = []
            for lang in ("ru", "ar"):
                for r in self._rows(conn, lang):
                    ex = conn.execute("SELECT source_text,target_text FROM term_example WHERE term_id=?",
                                      (r["id"],)).fetchall()
                    srcs = conn.execute("""SELECT s.code,s.title,s.kind,s.publisher,s.year,s.licence,s.note,ts.locator
                                           FROM term_source ts JOIN source_ref s ON s.id=ts.source_ref
                                           WHERE ts.term_id=?""", (r["id"],)).fetchall()
                    out.append({"lang": lang, "source": r["source"], "domain": r["domain"],
                                "preferred": r["preferred"], "acceptable": r["acceptable"],
                                "forbidden": r["forbidden"], "pos": r["pos"], "gender": r["gender"],
                                "number": r["number"], "grammar_note": r["grammar_note"],
                                "priority": r["priority"], "status": r["status"], "origin": r["origin"],
                                "version": r["version"],
                                "examples": [{"source": e[0], "target": e[1]} for e in ex],
                                "sources": [dict(s) for s in srcs]})
        Path(path).write_text(json.dumps({"format": "arabica-glossary", "version": 1, "terms": out},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        return len(out)

    def import_json(self, path: Path | str, origin: str = "import") -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if data.get("format") != "arabica-glossary":
            raise ValueError("Файл не является словарём Arabica (format=arabica-glossary)")
        n = 0
        for t in data.get("terms", []):
            t = dict(t)
            t.setdefault("origin", origin)
            self.add_term(t, reason="import")
            n += 1
        return n

    # ------------------------------------------------------------ history
    def log(self, source: str, target: str, direction: str, mode: str, model: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO history(ts,source,target,direction,mode,model) VALUES (?,?,?,?,?,?)",
                               (now(), source, target, direction, mode, model))
            return cur.lastrowid

    def recent(self, limit: int = 50, favourites_only: bool = False) -> list[dict]:
        q = "SELECT id,ts,source,target,direction,mode,favourite FROM history"
        if favourites_only:
            q += " WHERE favourite=1"
        with self._connect() as conn:
            rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_favourite(self, history_id: int, value: bool = True) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE history SET favourite=? WHERE id=?", (1 if value else 0, history_id))

    def clear_history(self, keep_favourites: bool = False) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history" + (" WHERE favourite=0" if keep_favourites else ""))
