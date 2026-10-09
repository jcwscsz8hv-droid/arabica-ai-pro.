-- Arabica AI Pro — local SQLite schema (glossary, history, favourites). Version 1.
-- Applied by arabica/glossary.py; also published as docs/GLOSSARY_SCHEMA.sql.
-- All data stays in %LOCALAPPDATA%\ArabicaAIPro\arabica.sqlite3. No network access.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

-- Bibliographic / provenance records for terms. A term without a source stays in status 'draft'.
CREATE TABLE IF NOT EXISTS source_ref (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,              -- short stable id, e.g. 'UNTERM', 'USER'
    title       TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('dictionary','termbase','official_document','standard',
                                                 'expert','user','proposal')),
    publisher   TEXT    NOT NULL DEFAULT '',
    year        TEXT    NOT NULL DEFAULT '',
    licence     TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT ''
);

-- A source-language term (one lexical unit in one domain).
CREATE TABLE IF NOT EXISTS term (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lang          TEXT    NOT NULL CHECK (lang IN ('ru','ar')),   -- language of `source`
    source        TEXT    NOT NULL,
    source_norm   TEXT    NOT NULL,           -- normalised for matching (lower-case / no tashkeel, unified alif)
    domain        TEXT    NOT NULL DEFAULT 'general'
                  CHECK (domain IN ('general','political','diplomatic','military','legal','economic',
                                    'science','technical','literary')),
    pos           TEXT    NOT NULL DEFAULT '' CHECK (pos IN ('','noun','verb','adj','adv','phrase','abbr','name')),
    gender        TEXT    NOT NULL DEFAULT '' CHECK (gender IN ('','m','f','n')),
    number        TEXT    NOT NULL DEFAULT '' CHECK (number IN ('','sg','du','pl','coll')),
    grammar_note  TEXT    NOT NULL DEFAULT '',  -- e.g. Arabic plural / root, Russian aspect pair
    priority      INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    bidirectional INTEGER NOT NULL DEFAULT 1 CHECK (bidirectional IN (0,1)),
    status        TEXT    NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','reviewed','approved','deprecated')),
    origin        TEXT    NOT NULL DEFAULT 'user' CHECK (origin IN ('user','builtin','import')),
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (lang, source_norm, domain)
);
CREATE INDEX IF NOT EXISTS ix_term_lookup ON term(lang, source_norm);

-- Target-language equivalents: exactly one 'preferred' per term (enforced in code + partial index).
CREATE TABLE IF NOT EXISTS equivalent (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id   INTEGER NOT NULL REFERENCES term(id) ON DELETE CASCADE,
    target    TEXT    NOT NULL,
    kind      TEXT    NOT NULL CHECK (kind IN ('preferred','acceptable','forbidden')),
    note      TEXT    NOT NULL DEFAULT '',        -- why acceptable/forbidden, register, context
    UNIQUE (term_id, target)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_preferred ON equivalent(term_id) WHERE kind = 'preferred';

CREATE TABLE IF NOT EXISTS term_example (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id      INTEGER NOT NULL REFERENCES term(id) ON DELETE CASCADE,
    source_text  TEXT    NOT NULL,
    target_text  TEXT    NOT NULL,
    source_ref   INTEGER REFERENCES source_ref(id)
);

CREATE TABLE IF NOT EXISTS term_source (
    term_id     INTEGER NOT NULL REFERENCES term(id) ON DELETE CASCADE,
    source_ref  INTEGER NOT NULL REFERENCES source_ref(id),
    locator     TEXT    NOT NULL DEFAULT '',     -- page / entry / document symbol
    PRIMARY KEY (term_id, source_ref)
);

-- Every change to a term is snapshotted (versioning / audit, local only).
CREATE TABLE IF NOT EXISTS term_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id     INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    changed_at  TEXT    NOT NULL,
    snapshot    TEXT    NOT NULL,                -- JSON of term + equivalents before the change
    reason      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    source     TEXT    NOT NULL,
    target     TEXT    NOT NULL,
    direction  TEXT    NOT NULL CHECK (direction IN ('ru-ar','ar-ru')),
    mode       TEXT    NOT NULL,
    model      TEXT    NOT NULL DEFAULT '',
    favourite  INTEGER NOT NULL DEFAULT 0 CHECK (favourite IN (0,1))
);
CREATE INDEX IF NOT EXISTS ix_history_ts ON history(ts);
