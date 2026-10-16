"""Job Watcher API: watch career pages and list the jobs found on them."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db, monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MIN_INTERVAL, MAX_INTERVAL = 1, 24 * 60
DEFAULT_INTERVAL = 1           # check every minute so new postings are caught quickly
DEFAULT_WINDOW = 30            # show jobs posted in the last 30 minutes


def _cutoff(posted_within: int) -> str:
    return db.to_iso(datetime.now(timezone.utc) - timedelta(minutes=posted_within))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    stop = asyncio.Event()
    task = asyncio.create_task(monitor.run_forever(stop))
    yield
    stop.set()
    await task


app = FastAPI(title="Job Watcher", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# When hosted on the internet, set JOB_WATCHER_PASSWORD so only you can use the site.
# The browser asks once (any username, this password) and remembers it.
PASSWORD = os.environ.get("JOB_WATCHER_PASSWORD", "")


@app.middleware("http")
async def require_password(request: Request, call_next):
    if not PASSWORD or request.url.path == "/api/health":
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            _, _, given = base64.b64decode(auth[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            given = ""
        if secrets.compare_digest(given.encode(), PASSWORD.encode()):
            return await call_next(request)
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Job Watcher"'})


# ── Models ───────────────────────────────────────────────────────────────────

def _normalize_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
        raise ValueError("Enter a full website address, e.g. https://example.com/careers")
    return value


class SiteCreate(BaseModel):
    url: str
    name: str | None = Field(default=None, max_length=120)
    interval_minutes: int = Field(default=DEFAULT_INTERVAL, ge=MIN_INTERVAL, le=MAX_INTERVAL)
    scan_now: bool = True   # bulk imports pass False and let the scheduler pace the first checks

    @field_validator("url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return _normalize_url(v)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=MIN_INTERVAL, le=MAX_INTERVAL)


class MarkSeen(BaseModel):
    site_id: int | None = None
    country: str = "all"   # limit to the jobs on screen: same values as the /api/jobs filters
    role: str = "all"


Country = Literal["india", "all"]
Role = Literal["tech", "all"]
# The default view: jobs in India, technical roles. Pass country=all / role=all for everything.
DEFAULT_COUNTRY: Country = "india"
DEFAULT_ROLE: Role = "tech"


def _view_filter(country: Country, role: Role) -> str:
    """SQL for the chosen view (built from fixed strings only, never from request text)."""
    parts = []
    if country == "india":
        parts.append("j.is_india = 1")
    if role == "tech":
        parts.append("j.is_tech = 1")
    return "".join(f" AND {p}" for p in parts)


def _site_select(country: Country, role: Role) -> str:
    """Sites with job counts. fresh_jobs / new_jobs only count jobs posted inside the window
    (?1 = cutoff) that match the view; active_jobs is everything listed on the site."""
    view = _view_filter(country, role)
    return f"""
SELECT s.*,
       (SELECT COUNT(*) FROM jobs j WHERE j.site_id = s.id AND j.is_active = 1) AS active_jobs,
       (SELECT COUNT(*) FROM jobs j WHERE j.site_id = s.id AND j.is_active = 1 AND j.fresh_at >= ?1{view}) AS fresh_jobs,
       (SELECT COUNT(*) FROM jobs j WHERE j.site_id = s.id AND j.is_active = 1 AND j.fresh_at >= ?1{view}
                                        AND j.is_seen = 0) AS new_jobs
FROM sites s
"""


def _site(site_id: int, posted_within: int = DEFAULT_WINDOW,
          country: Country = DEFAULT_COUNTRY, role: Role = DEFAULT_ROLE) -> dict:
    with db.connect() as conn:
        row = conn.execute(_site_select(country, role) + " WHERE s.id = ?2",
                           (_cutoff(posted_within), site_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Site not found")
    return dict(row)


def _default_name(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.")
    first_segment = parsed.path.strip("/").split("/")[0]
    # an ATS board's company lives in the path, e.g. jobs.lever.co/acme
    if first_segment and any(ats in host for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com")):
        return first_segment
    return host


# ── Sites ────────────────────────────────────────────────────────────────────

@app.get("/api/tech-roles")
def tech_roles() -> list[dict]:
    """What counts as a technical role: category headings and the roles under each."""
    from .classify import categories_for_display
    return categories_for_display()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/sites")
def list_sites(posted_within: int = Query(DEFAULT_WINDOW, ge=1, le=MAX_INTERVAL * 30),
               country: Country = DEFAULT_COUNTRY, role: Role = DEFAULT_ROLE) -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(_site_select(country, role) + " ORDER BY s.created_at DESC",
                                              (_cutoff(posted_within),))]


@app.get("/api/companies")
def list_companies(posted_within: int = Query(DEFAULT_WINDOW, ge=1, le=MAX_INTERVAL * 30),
                   country: Country = DEFAULT_COUNTRY, role: Role = DEFAULT_ROLE,
                   q: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    """One page of companies for the app, built to stay fast with many thousands of companies:
    companies with jobs in the window first, then ones with problems, then A–Z. Only recent jobs
    are counted (via the fresh_at index); "listed" is the job count from each company's last check."""
    view = _view_filter(country, role)
    cutoff = _cutoff(posted_within)
    fresh_sql = f"""SELECT j.site_id, COUNT(*) AS fresh_jobs, SUM(j.is_seen = 0) AS new_jobs FROM jobs j
                    WHERE j.is_active = 1 AND j.fresh_at >= ?{view} GROUP BY j.site_id"""
    where, params = "", []
    if q and q.strip():
        where = "WHERE s.name LIKE ? OR s.url LIKE ?"
        params = [f"%{q.strip()}%"] * 2
    with db.connect() as conn:
        rows = conn.execute(f"""
            SELECT s.*, COALESCE(s.last_job_count, 0) AS active_jobs,
                   COALESCE(f.fresh_jobs, 0) AS fresh_jobs, COALESCE(f.new_jobs, 0) AS new_jobs
            FROM sites s LEFT JOIN ({fresh_sql}) f ON f.site_id = s.id
            {where}
            ORDER BY (COALESCE(f.fresh_jobs, 0) > 0) DESC, (s.last_status IN ('error', 'empty')) DESC,
                     COALESCE(f.fresh_jobs, 0) DESC, s.name COLLATE NOCASE
            LIMIT ? OFFSET ?""", (cutoff, *params, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM sites s {where}", params).fetchone()[0]
        companies = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        fresh = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(j.is_seen = 0), 0) FROM jobs j "
                             f"WHERE j.is_active = 1 AND j.fresh_at >= ?{view}", (cutoff,)).fetchone()
    return {"total": total, "items": [dict(r) for r in rows],
            "summary": {"companies": companies, "fresh_jobs": fresh[0], "new_jobs": fresh[1]}}


@app.post("/api/sites", status_code=201)
def add_site(body: SiteCreate, background: BackgroundTasks) -> dict:
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM sites WHERE url = ?", (body.url,)).fetchone():
            raise HTTPException(409, "This website is already being watched")
        cur = conn.execute(
            "INSERT INTO sites (url, name, interval_minutes, created_at) VALUES (?, ?, ?, ?)",
            (body.url, (body.name or "").strip() or _default_name(body.url), body.interval_minutes, db.now_iso()),
        )
        site_id = cur.lastrowid
    if body.scan_now:
        background.add_task(monitor.scan_site, site_id)  # first scan right away
    return _site(site_id)


@app.patch("/api/sites/{site_id}")
def update_site(site_id: int, body: SiteUpdate) -> dict:
    _site(site_id)
    with db.connect() as conn:
        if body.name is not None and body.name.strip():
            conn.execute("UPDATE sites SET name = ? WHERE id = ?", (body.name.strip(), site_id))
        if body.interval_minutes is not None:
            conn.execute("UPDATE sites SET interval_minutes = ? WHERE id = ?", (body.interval_minutes, site_id))
    return _site(site_id)


@app.delete("/api/sites/{site_id}", status_code=204, response_class=Response)
def delete_site(site_id: int) -> Response:
    _site(site_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return Response(status_code=204)


@app.post("/api/sites/{site_id}/scan")
async def scan_now(site_id: int) -> dict:
    _site(site_id)
    await asyncio.to_thread(monitor.scan_site, site_id)
    return _site(site_id)


# ── Jobs ─────────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def list_jobs(posted_within: int = Query(DEFAULT_WINDOW, ge=1, le=MAX_INTERVAL * 30),
              country: Country = DEFAULT_COUNTRY, role: Role = DEFAULT_ROLE,
              site_id: int | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    """Open jobs posted in the last `posted_within` minutes, newest first, in India and
    technical by default (country=all / role=all widen it). Each job's `posted_at_source` says
    whether the time came from the site ('site') or is when Job Watcher first saw it ('found')."""
    where, params = ["j.is_active = 1", "j.fresh_at >= ?" + _view_filter(country, role)], [_cutoff(posted_within)]
    if site_id is not None:
        where.append("j.site_id = ?")
        params.append(site_id)
    if q and q.strip():
        where.append("(j.title LIKE ? OR j.location LIKE ? OR j.department LIKE ?)")
        params += [f"%{q.strip()}%"] * 3
    sql = ("SELECT j.*, s.name AS site_name FROM jobs j JOIN sites s ON s.id = j.site_id WHERE "
           + " AND ".join(where) + " ORDER BY j.fresh_at DESC, j.id DESC LIMIT ?")
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (*params, min(max(limit, 1), 2000)))]
    for r in rows:
        r["posted_at_source"] = "site" if db.exact_time(r["posted_at"]) == r["fresh_at"] else "found"
    return rows


@app.post("/api/jobs/mark-seen")
def mark_seen(body: MarkSeen) -> dict:
    view = _view_filter("india" if body.country == "india" else "all", "tech" if body.role == "tech" else "all")
    with db.connect() as conn:
        if body.site_id is None:
            cur = conn.execute(f"UPDATE jobs AS j SET is_seen = 1 WHERE is_seen = 0{view}")
        else:
            cur = conn.execute(f"UPDATE jobs AS j SET is_seen = 1 WHERE is_seen = 0 AND site_id = ?{view}",
                               (body.site_id,))
    return {"updated": cur.rowcount}


@app.post("/api/jobs/{job_id}/seen")
def mark_job_seen(job_id: int) -> dict:
    with db.connect() as conn:
        cur = conn.execute("UPDATE jobs SET is_seen = 1 WHERE id = ?", (job_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Job not found")
    return {"updated": 1}


# ── Website ──────────────────────────────────────────────────────────────────
# Serve the built frontend (frontend/dist) from the same server, so one always-on process
# gives you both the website and the API at http://localhost:8090. Mounted last so /api wins.
FRONTEND_DIST = Path(os.environ.get(
    "JOB_WATCHER_FRONTEND", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
