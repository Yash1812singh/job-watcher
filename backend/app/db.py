"""SQLite storage for watched sites and the jobs found on them.

One connection per call keeps this safe across FastAPI's worker threads and the
background monitor thread; SQLite handles the locking.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

# Kept outside Desktop/Documents: macOS doesn't let background services read those folders,
# and both the background service and ./run.sh should see the same companies and jobs.
DATA_DIR = os.path.expanduser("~/Library/Application Support/JobWatcher")
DB_PATH = os.environ.get("JOB_WATCHER_DB", os.path.join(DATA_DIR, "jobwatcher.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url              TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 15,
    created_at       TEXT NOT NULL,
    last_checked_at  TEXT,
    last_status      TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | empty | error
    last_error       TEXT,
    last_method      TEXT,                              -- how jobs were found, e.g. greenhouse, json-ld, links
    last_job_count   INTEGER,
    baseline_done    INTEGER NOT NULL DEFAULT 0         -- 1 once the jobs already listed when added are recorded
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id       INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    location      TEXT,
    department    TEXT,
    posted_at     TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,           -- 0 once the posting disappears from the site
    is_seen       INTEGER NOT NULL DEFAULT 0,           -- 0 = shows as NEW in the UI
    is_baseline   INTEGER NOT NULL DEFAULT 0,           -- 1 = already listed when the company was added
    fresh_at      TEXT,                                 -- best known posting time (UTC); NULL = unknown/old
    is_india      INTEGER NOT NULL DEFAULT 0,           -- location looks like India (app/classify.py)
    is_tech       INTEGER NOT NULL DEFAULT 0,           -- title/team looks like a technical role
    tech_category TEXT,                                 -- e.g. "Software Engineering"; NULL if not technical
    UNIQUE (site_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first release; ALTERed into existing databases on startup.
MIGRATIONS = [
    ("sites", "baseline_done", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "is_baseline", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "fresh_at", "TEXT"),
    ("jobs", "is_india", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "is_tech", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "tech_category", "TEXT"),
]


def now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def to_iso(dt: datetime) -> str:
    """One fixed UTC format, so timestamps compare correctly as strings in SQL."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def exact_time(value: str | None) -> str | None:
    """Normalize a posting time to UTC ISO, or None when it's missing or date-only
    (a date alone can't tell whether a job was posted in the last 30 minutes)."""
    if not value or len(value) <= 10:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_iso(dt)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        added = []
        for table, column, decl in MIGRATIONS:
            exists = conn.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")} if exists else set()
            if exists and column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                added.append(column)
        conn.executescript(SCHEMA)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_fresh ON jobs(fresh_at DESC)")
        # per-company counts on every page refresh; matters with 1000+ companies
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_site_active ON jobs(site_id, is_active, fresh_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_default_view ON jobs(is_india, is_tech, is_active, fresh_at)")
        retag(conn)
        if "fresh_at" in added:
            # jobs saved before this change were all there when their company was first checked
            conn.execute("UPDATE jobs SET is_baseline = 1")
            for row in conn.execute("SELECT id, posted_at FROM jobs").fetchall():
                conn.execute("UPDATE jobs SET fresh_at = ? WHERE id = ?", (exact_time(row["posted_at"]), row["id"]))
            conn.execute("UPDATE sites SET baseline_done = 1 WHERE id IN (SELECT DISTINCT site_id FROM jobs)")


def retag(conn: sqlite3.Connection, force: bool = False) -> int:
    """Re-apply the India / technical-role rules to every saved job when the rules have changed
    since the jobs were tagged (or always, with force). Returns how many jobs were re-tagged."""
    from .classify import RULES_VERSION, is_india, tech_category
    row = conn.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()
    if not force and row and row["value"] == RULES_VERSION:
        return 0
    rows = conn.execute("SELECT id, title, location, department FROM jobs").fetchall()
    updates = []
    for r in rows:
        category = tech_category(r["title"], r["department"])
        updates.append((int(is_india(r["location"])), int(category is not None), category, r["id"]))
    conn.executemany("UPDATE jobs SET is_india = ?, is_tech = ?, tech_category = ? WHERE id = ?", updates)
    conn.execute("INSERT INTO meta (key, value) VALUES ('rules_version', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (RULES_VERSION,))
    return len(updates)
