"""Scan sites and keep the jobs table in sync, on a schedule and on demand."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from . import db
from .classify import is_india, tech_category
from .scraper import ScanError, scan

log = logging.getLogger("jobwatcher.monitor")

TICK_SECONDS = 10          # how often the loop looks for sites that are due
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
    with _lock_for(site_id):
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
                conn.execute(
                    "UPDATE sites SET last_checked_at = ?, last_status = 'error', last_error = ? WHERE id = ?",
                    (checked_at, str(e), site_id),
                )
            return
        except Exception as e:  # a parsing bug shouldn't kill the monitor loop
            log.exception("unexpected scan error site=%s", site_id)
            with db.connect() as conn:
                conn.execute(
                    "UPDATE sites SET last_checked_at = ?, last_status = 'error', last_error = ? WHERE id = ?",
                    (checked_at, f"Unexpected error: {e}", site_id),
                )
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
        rows = conn.execute("SELECT id, interval_minutes, last_checked_at FROM sites").fetchall()
    due = []
    for r in rows:
        last = r["last_checked_at"]
        if last is None:
            due.append((float("inf"), r["id"]))
            continue
        waited = (now - datetime.fromisoformat(last)) / timedelta(minutes=r["interval_minutes"])
        if waited >= 1:
            due.append((waited, r["id"]))
    # When there's more to check than time allows, the most overdue go first, relative to
    # their interval: a 2-minute company 6 minutes late is further behind than a 5-minute one.
    return [site_id for _, site_id in sorted(due, key=lambda d: -d[0])]


async def run_forever(stop: asyncio.Event) -> None:
    """Check every due company, several at a time, so one slow site (e.g. one that needs
    the browser) doesn't delay catching a new posting elsewhere."""
    log.info("monitor started (tick %ss, %s parallel)", TICK_SECONDS, MAX_PARALLEL_SCANS)
    running: dict[int, asyncio.Task] = {}
    stopping = asyncio.create_task(stop.wait())

    while not stop.is_set():
        # Start checks only as slots free up, picking the most overdue each time, so a company
        # that falls due later can still go ahead of ones that are less behind.
        for sid in [sid for sid, t in running.items() if t.done()]:
            del running[sid]
        free = MAX_PARALLEL_SCANS - len(running)
        if free > 0:
            for site_id in [sid for sid in due_site_ids() if sid not in running][:free]:
                running[site_id] = asyncio.create_task(asyncio.to_thread(scan_site, site_id))
        # wake when a check finishes (a slot is free) or after a tick, whichever comes first
        await asyncio.wait([stopping, *running.values()], timeout=TICK_SECONDS,
                           return_when=asyncio.FIRST_COMPLETED)
    stopping.cancel()
    # Don't wait for checks in progress (a browser check can take ~45 s): an interrupted check
    # writes nothing and simply runs again after the restart.
    log.info("monitor stopped (%d check(s) left unfinished)", len(running))
