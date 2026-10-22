"""Scan sites and keep the jobs table in sync, on a schedule and on demand."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from .classify import is_india, tech_category
from .scraper import ScanError, scan

log = logging.getLogger("jobwatcher.monitor")

TICK_SECONDS = 10          # how often the loop looks for sites that are due
# A check that takes longer than this is abandoned (its slot goes to the next company) and
# retried at the next interval. Some sites hang without ever hitting the HTTP timeouts: a
# server that trickles bytes, a DNS lookup that never answers, a maintenance page.
SCAN_TIMEOUT = int(os.environ.get("JOB_WATCHER_SCAN_TIMEOUT", "180"))
# No check finishing for this long means the checker is stuck; /api/health then fails so the
# host restarts the service.
STALL_SECONDS = int(os.environ.get("JOB_WATCHER_STALL_SECONDS", "900"))
_last_progress = time.monotonic()
_abandoned = 0  # checks given up on that may still be running in the background


def _progress() -> None:
    global _last_progress
    _last_progress = time.monotonic()


def checker_status() -> dict:
    stalled_for = time.monotonic() - _last_progress
    return {"stalled": stalled_for > STALL_SECONDS, "seconds_since_last_check": round(stalled_for), "abandoned_checks": _abandoned,
            **_backlog}


# How far behind schedule the checks are (refreshed by the loop): with too many companies for the
# server, "overdue" grows and the oldest wait gets long.
_backlog: dict = {"companies": 0, "overdue": 0, "most_overdue_minutes": 0}
# One free server can't check thousands of companies every couple of minutes, so each company is
# checked as often as it's worth (its own interval is the fastest it goes). Only tech hiring in
# India matters here:
#   a tech job in India posted in the last RECENT_DAYS     -> its interval (actively hiring)
#   tech jobs open in India, none posted lately            -> every WARM_MINUTES (still inside the 30-minute window)
#   none                                                  -> every COLD_MINUTES
#   failing                                               -> twice as long after each failure, up to MAX_BACKOFF_MINUTES
WARM_MINUTES = int(os.environ.get("JOB_WATCHER_WARM_MINUTES", "15"))
# the fastest any company is checked, whatever its interval says (a 2-minute check costs much and
# gains little when jobs show for 30 minutes); 1 on a bigger server keeps every interval as set
FASTEST_MINUTES = int(os.environ.get("JOB_WATCHER_FASTEST_MINUTES", "5"))
COLD_MINUTES = int(os.environ.get("JOB_WATCHER_COLD_MINUTES", "60"))
MAX_BACKOFF_MINUTES = int(os.environ.get("JOB_WATCHER_MAX_BACKOFF_MINUTES", "360"))
RECENT_DAYS = 7


def pace(conn, site_id: int) -> int:
    """How often to check a company that was just checked successfully, from what it's hiring for."""
    chosen = conn.execute("SELECT interval_minutes FROM sites WHERE id = ?", (site_id,)).fetchone()["interval_minutes"]
    recent = db.to_iso(datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS))
    # fresh_at: when it was posted (exact time from the site) or first seen after the company was added
    row = conn.execute(
        """SELECT COALESCE(MAX(is_active), 0) AS open, COALESCE(MAX(fresh_at >= ?), 0) AS lately
           FROM jobs WHERE site_id = ? AND is_india = 1 AND is_tech = 1""", (recent, site_id)).fetchone()
    if row["lately"]:
        return max(chosen, FASTEST_MINUTES)
    return max(chosen, WARM_MINUTES if row["open"] else COLD_MINUTES)


def _failed(conn, site_id: int, checked_at: str, error: str) -> None:
    """Record a failed check and wait longer before the next one (twice as long each time in a row)."""
    row = conn.execute("SELECT interval_minutes, effective_interval, error_streak FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        return
    base = row["effective_interval"] or row["interval_minutes"]
    conn.execute(
        """UPDATE sites SET last_checked_at = ?, last_status = 'error', last_error = ?, error_streak = ?,
                  effective_interval = ? WHERE id = ?""",
        (checked_at, error, row["error_streak"] + 1, min(max(base * 2, row["interval_minutes"]), MAX_BACKOFF_MINUTES), site_id))


# companies checked at the same time (browser checks are capped separately); lower it on small servers
MAX_PARALLEL_SCANS = int(os.environ.get("JOB_WATCHER_MAX_PARALLEL", "24"))
_site_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(site_id: int) -> threading.Lock:
    with _locks_guard:
        return _site_locks.setdefault(site_id, threading.Lock())


def scan_site(site_id: int) -> None:
    """Scan one site and record what changed. Safe to call from any thread; overlapping
    scans of the same site wait for each other instead of double-inserting."""
    lock = _lock_for(site_id)
    # A check that hung keeps holding its site's lock; don't queue up behind it forever.
    if not lock.acquire(timeout=60):
        log.warning("site=%s is still being checked by an earlier check; skipping", site_id)
        return
    try:
        _scan_site_locked(site_id)
    finally:
        lock.release()
        _progress()


def _scan_site_locked(site_id: int) -> None:
    with db.connect() as conn:
        site = conn.execute("SELECT id, url FROM sites WHERE id = ?", (site_id,)).fetchone()
    if site is None:
        return

    checked_at = db.now_iso()
    try:
        result = scan(site["url"])
    except ScanError as e:
        log.warning("scan failed site=%s: %s", site_id, e)
        with db.connect() as conn:
            _failed(conn, site_id, checked_at, str(e))
        return
    except Exception as e:  # a parsing bug shouldn't kill the monitor loop
        log.exception("unexpected scan error site=%s", site_id)
        with db.connect() as conn:
            _failed(conn, site_id, checked_at, f"Unexpected error: {e}")
        return

    with db.connect() as conn:
        site_row = conn.execute(
            "SELECT baseline_done, last_method FROM sites WHERE id = ?", (site_id,)).fetchone()
        stored = {r["external_id"]: r for r in conn.execute(
            """SELECT external_id, title, url, location, department, posted_at, is_active
               FROM jobs WHERE site_id = ?""", (site_id,))}
        existing = set(stored)
        active_before = {eid for eid, r in stored.items() if r["is_active"]}
        unique_jobs = list({job.external_id: job for job in result.jobs}.values())
        found_ids = {job.external_id for job in unique_jobs}

        # The first scan that finds jobs records what was already listed. Those jobs only count
        # as fresh if the site gives an exact posting time; later arrivals are fresh from the
        # moment they're first seen. Re-record the baseline when the page starts being read
        # differently (e.g. menu links before, real postings now) instead of calling every
        # job on it new: a company doesn't replace its whole list in one go.
        reread = site_row["baseline_done"] and len(found_ids) >= 3 and (
            (active_before and not (found_ids & active_before))
            or (_method_family(site_row["last_method"]) != _method_family(result.method)
                # "none" after earlier jobs means the page couldn't be read for a while
                # (e.g. no browser), not that the company just posted its whole list
                and (_method_family(site_row["last_method"]) is not None or existing)
                and len(found_ids - existing) > len(found_ids) / 2))
        if reread:
            log.info("site=%s is now read differently (%s -> %s); treating its listed jobs as existing",
                     site_id, site_row["last_method"], result.method)
        baseline_done = site_row["baseline_done"] and not reread
        is_baseline = 0 if baseline_done else 1
        seen_now = set()
        new_count = 0
        for job in unique_jobs:
            seen_now.add(job.external_id)
            exact = db.exact_time(job.posted_at)
            if job.external_id in existing:
                old = stored[job.external_id]
                # a feed without posting times (e.g. Ashby's job list) keeps the one already stored
                job.posted_at = job.posted_at or old["posted_at"]
                # Only write when something changed; big boards list thousands of jobs a check.
                if (old["is_active"], old["title"], old["url"], old["location"], old["department"],
                        old["posted_at"]) != (1, job.title, job.url, job.location, job.department, job.posted_at):
                    conn.execute(
                        """UPDATE jobs SET title = ?, url = ?, location = ?, department = ?, posted_at = ?,
                                  fresh_at = COALESCE(?, fresh_at), is_active = 1,
                                  is_india = ?, is_tech = ?, tech_category = ?
                           WHERE site_id = ? AND external_id = ?""",
                        (job.title, job.url, job.location, job.department, job.posted_at, exact,
                         *_tags(job), site_id, job.external_id),
                    )
            else:
                new_count += 1
                fresh_at = exact or (None if is_baseline else checked_at)
                conn.execute(
                    """INSERT INTO jobs (site_id, external_id, title, url, location, department, posted_at,
                                         first_seen_at, last_seen_at, is_baseline, fresh_at, is_seen,
                                         is_india, is_tech, tech_category)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (site_id, job.external_id, job.title, job.url, job.location, job.department,
                     job.posted_at, checked_at, checked_at, is_baseline, fresh_at,
                     1 if fresh_at is None else 0,
                     *_tags(job)),
                )
        if seen_now and not baseline_done:
            conn.execute("UPDATE sites SET baseline_done = 1 WHERE id = ?", (site_id,))
        # postings that disappeared from the site are kept but marked closed
        gone = active_before - seen_now
        conn.executemany("UPDATE jobs SET is_active = 0 WHERE site_id = ? AND external_id = ?",
                         [(site_id, eid) for eid in gone])
        conn.execute("UPDATE jobs SET last_seen_at = ? WHERE site_id = ? AND is_active = 1",
                     (checked_at, site_id))
        conn.execute(
            """UPDATE sites SET last_checked_at = ?, last_status = ?, last_error = ?, last_method = ?,
                      last_job_count = ? WHERE id = ?""",
            (checked_at, "ok" if result.jobs else "empty", result.note, result.method,
             len(seen_now), site_id),
        )
        conn.execute("UPDATE sites SET error_streak = 0, effective_interval = ? WHERE id = ?", (pace(conn, site_id), site_id))
    log.info("scanned site=%s method=%s jobs=%d new=%d", site_id, result.method, len(seen_now), new_count)


def _tags(job) -> tuple[int, int, str | None]:
    """(is_india, is_tech, tech_category) for a job, as stored in the jobs table."""
    category = tech_category(job.title, job.department)
    return int(is_india(job.location)), int(category is not None), category


def _method_family(method: str | None) -> str | None:
    """'links (rendered)' and 'links' are the same way of reading a page."""
    if not method or method == "none":
        return None
    return method.replace(" (rendered)", "")


def due_site_ids() -> list[int]:
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        rows = conn.execute("""SELECT id, COALESCE(effective_interval, MAX(interval_minutes, ?)) AS interval_minutes,
                                      last_checked_at, created_at FROM sites""", (FASTEST_MINUTES,)).fetchall()
    due = []
    worst = 0.0
    for r in rows:
        last = r["last_checked_at"]
        if last is None:
            # a company never checked waits its turn from when it was added, like one that was due
            # then: adding hundreds at once neither holds up the others nor leaves them never checked
            waited = (now - datetime.fromisoformat(r["created_at"])) / timedelta(minutes=r["interval_minutes"])
            due.append((max(waited, 1.0), r["id"]))
            continue
        waited = (now - datetime.fromisoformat(last)) / timedelta(minutes=r["interval_minutes"])
        if waited >= 1:
            due.append((waited, r["id"]))
            worst = max(worst, (waited - 1) * r["interval_minutes"])
    _backlog.update(companies=len(rows), overdue=len(due), most_overdue_minutes=round(worst))
    # When there's more to check than time allows, the most overdue go first, relative to
    # their interval: a 2-minute company 6 minutes late is further behind than a 5-minute one.
    return [site_id for _, site_id in sorted(due, key=lambda d: -d[0])]


def _start_check(site_id: int) -> asyncio.Future:
    """Run one check on its own daemon thread. Unlike a shared thread pool, a check that never
    returns can't take a worker away from the others."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()

    def work():
        try:
            scan_site(site_id)
        except Exception:  # scan_site handles scan errors itself; this is a last resort
            log.exception("check of site=%s crashed", site_id)
        finally:
            loop.call_soon_threadsafe(lambda: done.done() or done.set_result(None))

    threading.Thread(target=work, name=f"check-{site_id}", daemon=True).start()
    return done


async def _check(site_id: int) -> None:
    global _abandoned
    try:
        await asyncio.wait_for(_start_check(site_id), SCAN_TIMEOUT)
    except asyncio.TimeoutError:
        _abandoned += 1
        log.warning("site=%s: check still running after %ss; moving on (will retry next interval)", site_id, SCAN_TIMEOUT)
        with db.connect() as conn:
            _failed(conn, site_id, db.now_iso(), f"The site didn't answer within {SCAN_TIMEOUT // 60} minutes; trying again at the next check.")
        _progress()


async def run_forever(stop: asyncio.Event) -> None:
    """Check every due company, several at a time, so one slow site (e.g. one that needs
    the browser) doesn't delay catching a new posting elsewhere."""
    log.info("monitor started (tick %ss, %s parallel, %ss limit per check)", TICK_SECONDS, MAX_PARALLEL_SCANS, SCAN_TIMEOUT)
    _progress()
    running: dict[int, asyncio.Task] = {}
    stopping = asyncio.create_task(stop.wait())

    while not stop.is_set():
        try:
            # Start checks only as slots free up, picking the most overdue each time, so a company
            # that falls due later can still go ahead of ones that are less behind.
            for sid in [sid for sid, t in running.items() if t.done()]:
                del running[sid]
            free = MAX_PARALLEL_SCANS - len(running)
            if free > 0:
                for site_id in [sid for sid in due_site_ids() if sid not in running][:free]:
                    running[site_id] = asyncio.create_task(_check(site_id))
            # wake when a check finishes (a slot is free) or after a tick, whichever comes first
            await asyncio.wait([stopping, *running.values()], timeout=TICK_SECONDS,
                               return_when=asyncio.FIRST_COMPLETED)
        except Exception:  # e.g. the database briefly locked: never let the checker die
            log.exception("monitor loop error; continuing")
            await asyncio.sleep(TICK_SECONDS)
    stopping.cancel()
    # Don't wait for checks in progress (a browser check can take ~45 s): an interrupted check
    # writes nothing and simply runs again after the restart.
    log.info("monitor stopped (%d check(s) left unfinished)", len(running))
