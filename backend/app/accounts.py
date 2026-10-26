"""Accounts for Fill with AI: anyone can sign up, keep their own profile and resume, and create
keys for the browser extension. Each user only ever sees their own data.

Passwords are stored as scrypt hashes; session and extension tokens only as SHA-256 hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS fill_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fill_tokens (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES fill_users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,              -- session (the website) | extension
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS fill_profiles (
    user_id           INTEGER PRIMARY KEY REFERENCES fill_users(id) ON DELETE CASCADE,
    data              TEXT NOT NULL DEFAULT '{}',
    resume            BLOB,
    resume_name       TEXT,
    resume_updated_at TEXT,
    updated_at        TEXT NOT NULL
);
-- jobs a user opened with Fill with AI, and whether the extension saw them submitted
CREATE TABLE IF NOT EXISTS fill_applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES fill_users(id) ON DELETE CASCADE,
    job_id     INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    url        TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    company    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,                -- opened | filled (ready for the user to submit) | submitted
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}',   -- what was filled, the resume used, what was left for the user
    proof      BLOB,                         -- screenshot of the confirmation page
    submitted_at TEXT,
    UNIQUE (user_id, job_id)
);
-- the user's resume read into sections (see tailor.py), redone when the resume changes
CREATE TABLE IF NOT EXISTS fill_resume_sections (
    user_id     INTEGER PRIMARY KEY REFERENCES fill_users(id) ON DELETE CASCADE,
    resume_hash TEXT NOT NULL,
    text        TEXT NOT NULL,
    sections    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
-- the resume tailored to a job, kept so the same job gets the same resume
CREATE TABLE IF NOT EXISTS fill_tailored (
    user_id    INTEGER NOT NULL REFERENCES fill_users(id) ON DELETE CASCADE,
    job_key    TEXT NOT NULL,
    version    TEXT NOT NULL,               -- resume + skills it was made from
    pdf        BLOB NOT NULL,
    added      TEXT NOT NULL DEFAULT '[]',
    missing    TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_key)
);
"""
# columns added to tables created by an earlier version
MIGRATIONS = [
    ("fill_applications", "details", "TEXT NOT NULL DEFAULT '{}'"),
    ("fill_applications", "proof", "BLOB"),
    ("fill_applications", "submitted_at", "TEXT"),
]

MAX_RESUME_BYTES = 5 * 1024 * 1024


def init() -> None:
    with db.connect() as conn:
        for table, column, decl in MIGRATIONS:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists and column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.executescript(SCHEMA)


class AccountError(Exception):
    pass


# ── Passwords and tokens ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(user_id: int, kind: str, label: str = "") -> str:
    token = ("fk_" if kind == "extension" else "fs_") + secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute("INSERT INTO fill_tokens (token_hash, user_id, kind, label, created_at) VALUES (?, ?, ?, ?, ?)",
                     (_token_hash(token), user_id, kind, label, db.now_iso()))
    return token


def user_for_token(token: str, kinds: tuple[str, ...] = ("session", "extension")) -> dict | None:
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute("""SELECT u.id, u.email, u.name, t.kind, t.token_hash FROM fill_tokens t
                              JOIN fill_users u ON u.id = t.user_id WHERE t.token_hash = ?""", (_token_hash(token),)).fetchone()
        if row is None or row["kind"] not in kinds:
            return None
        conn.execute("UPDATE fill_tokens SET last_used_at = ? WHERE token_hash = ?", (db.now_iso(), row["token_hash"]))
    return {"id": row["id"], "email": row["email"], "name": row["name"], "via": row["kind"]}


def drop_token(token: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM fill_tokens WHERE token_hash = ?", (_token_hash(token),))


# ── Users ────────────────────────────────────────────────────────────────────

def sign_up(name: str, email: str, password: str) -> tuple[dict, str]:
    if len(password) < 8:
        raise AccountError("Use a password of at least 8 characters.")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM fill_users WHERE email = ?", (email,)).fetchone():
            raise AccountError("An account with this email already exists. Log in instead.")
        uid = conn.execute("INSERT INTO fill_users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                           (email, name.strip(), hash_password(password), db.now_iso())).lastrowid
        first, _, last = name.strip().partition(" ")
        start = {"personal": {"first_name": first, "last_name": last, "email": email}}
        conn.execute("INSERT INTO fill_profiles (user_id, data, updated_at) VALUES (?, ?, ?)", (uid, json.dumps(start), db.now_iso()))
    return {"id": uid, "email": email, "name": name.strip()}, new_token(uid, "session")


def log_in(email: str, password: str) -> tuple[dict, str]:
    with db.connect() as conn:
        row = conn.execute("SELECT id, email, name, password_hash FROM fill_users WHERE email = ?", (email,)).fetchone()
    if row is None or not check_password(password, row["password_hash"]):
        raise AccountError("Wrong email or password.")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}, new_token(row["id"], "session")


def delete_user(user_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM fill_applications WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM fill_resume_sections WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM fill_tailored WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM fill_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM fill_profiles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM fill_users WHERE id = ?", (user_id,))


# ── Profile and resume ───────────────────────────────────────────────────────

def get_profile(user_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT data, resume_name, resume_updated_at, length(resume) AS size, updated_at FROM fill_profiles WHERE user_id = ?",
                           (user_id,)).fetchone()
    if row is None:
        return {"data": {}, "resume": None, "updated_at": None}
    resume = {"name": row["resume_name"], "size": row["size"], "updated_at": row["resume_updated_at"]} if row["size"] else None
    return {"data": json.loads(row["data"] or "{}"), "resume": resume, "updated_at": row["updated_at"]}


def save_profile(user_id: int, data: dict) -> None:
    with db.connect() as conn:
        conn.execute("""INSERT INTO fill_profiles (user_id, data, updated_at) VALUES (?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
                     (user_id, json.dumps(data), db.now_iso()))


def save_resume(user_id: int, name: str, content: bytes) -> None:
    if not content.startswith(b"%PDF"):
        raise AccountError("Upload your resume as a PDF.")
    if len(content) > MAX_RESUME_BYTES:
        raise AccountError("That file is over 5 MB.")
    with db.connect() as conn:
        conn.execute("""INSERT INTO fill_profiles (user_id, resume, resume_name, resume_updated_at, updated_at) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET resume = excluded.resume, resume_name = excluded.resume_name,
                        resume_updated_at = excluded.resume_updated_at""",
                     (user_id, content, name or "resume.pdf", db.now_iso(), db.now_iso()))


def get_resume(user_id: int) -> tuple[str, bytes] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT resume_name, resume FROM fill_profiles WHERE user_id = ?", (user_id,)).fetchone()
    return (row["resume_name"], bytes(row["resume"])) if row and row["resume"] else None


def extension_keys(user_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("""SELECT substr(token_hash, 1, 12) AS id, label, created_at, last_used_at FROM fill_tokens
                               WHERE user_id = ? AND kind = 'extension' ORDER BY created_at DESC""", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def drop_extension_key(user_id: int, key_id: str) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM fill_tokens WHERE user_id = ? AND kind = 'extension' AND token_hash LIKE ?",
                           (user_id, key_id + "%"))
    return cur.rowcount > 0


# ── Applications ─────────────────────────────────────────────────────────────

def job_info(job_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("""SELECT j.id, j.title, j.url, j.location, s.name AS company, s.url AS site_url
                              FROM jobs j JOIN sites s ON s.id = j.site_id WHERE j.id = ?""", (job_id,)).fetchone()
    return dict(row) if row else None


STATUS_ORDER = {"opened": 0, "filled": 1, "submitted": 2}


def record_application(user_id: int, job_id: int | None, status: str, url: str = "", title: str = "", company: str = "",
                       details: dict | None = None, proof: bytes | None = None) -> None:
    """opened when the panel starts on a job's form; filled when every page it could do is done and
    the form is ready for the user to submit; submitted when it sees the confirmation page. Never goes
    back (submitted stays submitted). details are merged into what's recorded; lists are added to."""
    if job_id:
        info = job_info(job_id)
        if info:
            url, title, company = url or info["url"], info["title"], info["company"]
    if not url:
        return
    now = db.now_iso()
    with db.connect() as conn:
        existing = conn.execute("SELECT id, status, details FROM fill_applications WHERE user_id = ? AND "
                                + ("job_id = ?" if job_id else "job_id IS NULL AND url = ?"), (user_id, job_id or url)).fetchone()
        if existing:
            if STATUS_ORDER.get(status, 0) < STATUS_ORDER.get(existing["status"], 0):
                status = existing["status"]
            merged = _merge(json.loads(existing["details"] or "{}"), details or {})
            conn.execute("""UPDATE fill_applications SET status = ?, updated_at = ?, details = ?,
                            proof = COALESCE(?, proof), submitted_at = COALESCE(submitted_at, ?) WHERE id = ?""",
                         (status, now, json.dumps(merged), proof, now if status == "submitted" else None, existing["id"]))
        else:
            conn.execute("""INSERT INTO fill_applications (user_id, job_id, url, title, company, status, created_at, updated_at,
                                                          details, proof, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (user_id, job_id, url, title, company, status, now, now, json.dumps(details or {}), proof,
                          now if status == "submitted" else None))


def _merge(old: dict, new: dict) -> dict:
    """Details from several pages of one application: what was filled adds up page by page (without
    repeats); the rest (what's left for the user, the resume used…) is the latest."""
    out = dict(old)
    for k, v in new.items():
        if v is None:                     # a later page that didn't see it (e.g. the confirmation page)
            continue
        if k == "filled" and isinstance(v, list) and isinstance(out.get(k), list):
            seen = {json.dumps(x, sort_keys=True) for x in out[k]}
            out[k] = out[k] + [x for x in v if json.dumps(x, sort_keys=True) not in seen]
        else:
            out[k] = v
    return out


def applications(user_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("""SELECT id, job_id, url, title, company, status, created_at, updated_at, submitted_at, details,
                                      proof IS NOT NULL AS has_proof
                               FROM fill_applications WHERE user_id = ? ORDER BY updated_at DESC LIMIT 500""", (user_id,)).fetchall()
    return [{**dict(r), "details": json.loads(r["details"] or "{}"), "has_proof": bool(r["has_proof"])} for r in rows]


def application_proof(user_id: int, application_id: int) -> bytes | None:
    with db.connect() as conn:
        row = conn.execute("SELECT proof FROM fill_applications WHERE id = ? AND user_id = ?", (application_id, user_id)).fetchone()
    return bytes(row["proof"]) if row and row["proof"] else None


# ── Tailored resumes ─────────────────────────────────────────────────────────

def resume_sections(user_id: int) -> tuple[str, str, dict] | None:
    """(resume hash, text, sections) as last read, or None."""
    with db.connect() as conn:
        row = conn.execute("SELECT resume_hash, text, sections FROM fill_resume_sections WHERE user_id = ?", (user_id,)).fetchone()
    return (row["resume_hash"], row["text"], json.loads(row["sections"])) if row else None


def save_resume_sections(user_id: int, resume_hash: str, text: str, sections: dict) -> None:
    with db.connect() as conn:
        conn.execute("""INSERT INTO fill_resume_sections (user_id, resume_hash, text, sections, created_at) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET resume_hash = excluded.resume_hash, text = excluded.text,
                        sections = excluded.sections, created_at = excluded.created_at""",
                     (user_id, resume_hash, text, json.dumps(sections), db.now_iso()))


def tailored(user_id: int, job_key: str, version: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT pdf, added, missing FROM fill_tailored WHERE user_id = ? AND job_key = ? AND version = ?",
                           (user_id, job_key, version)).fetchone()
    return {"pdf": bytes(row["pdf"]), "added": json.loads(row["added"]), "missing": json.loads(row["missing"])} if row else None


def save_tailored(user_id: int, job_key: str, version: str, pdf: bytes, added: list, missing: list) -> None:
    with db.connect() as conn:
        conn.execute("""INSERT INTO fill_tailored (user_id, job_key, version, pdf, added, missing, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, job_key) DO UPDATE SET version = excluded.version, pdf = excluded.pdf,
                        added = excluded.added, missing = excluded.missing, created_at = excluded.created_at""",
                     (user_id, job_key, version, pdf, json.dumps(added), json.dumps(missing), db.now_iso()))
