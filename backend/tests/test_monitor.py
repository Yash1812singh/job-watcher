import time
from datetime import datetime, timedelta, timezone

import pytest

from app import db, monitor
from app.scraper import Job, ScanError, ScanResult


@pytest.fixture
def site_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO sites (url, name, created_at) VALUES ('https://acme.com/jobs', 'acme', ?)",
                           (db.now_iso(),))
        return cur.lastrowid


def _scan_returns(monkeypatch, *jobs, error=None):
    def fake_scan(url):
        if error:
            raise ScanError(error)
        return ScanResult(method="test", jobs=list(jobs))
    monkeypatch.setattr(monitor, "scan", fake_scan)


def _jobs():
    with db.connect() as conn:
        return {r["external_id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}


def _minutes_ago(m: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat().replace("+00:00", "Z")


def test_jobs_listed_when_added_are_old_and_later_arrivals_are_fresh(site_id, monkeypatch):
    _scan_returns(monkeypatch, Job("1", "Engineer", "u1"), Job("2", "Designer", "u2"))
    monitor.scan_site(site_id)
    jobs = _jobs()
    assert all(j["is_baseline"] == 1 and j["fresh_at"] is None and j["is_seen"] == 1 for j in jobs.values())

    time.sleep(1.1)  # timestamps have one-second resolution
    _scan_returns(monkeypatch, Job("1", "Senior Engineer", "u1"), Job("3", "Analyst", "u3"))
    monitor.scan_site(site_id)

    jobs = _jobs()
    assert jobs["1"]["title"] == "Senior Engineer" and jobs["1"]["fresh_at"] is None   # still old
    assert jobs["2"]["is_active"] == 0                                                 # gone -> closed
    new = jobs["3"]
    assert (new["is_baseline"], new["is_seen"]) == (0, 0)
    assert new["fresh_at"] == new["first_seen_at"]                                     # fresh from when it was seen
    with db.connect() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    assert (site["last_status"], site["last_job_count"], site["baseline_done"]) == ("ok", 2, 1)


def test_exact_posting_time_from_the_site_wins(site_id, monkeypatch):
    _scan_returns(monkeypatch,
                  Job("recent", "Posted 5 min ago", "u1", posted_at=_minutes_ago(5)),
                  Job("old", "Posted 2 days ago", "u2", posted_at=_minutes_ago(2 * 24 * 60)),
                  Job("dated", "Date only", "u3", posted_at="2026-10-16"))
    monitor.scan_site(site_id)   # first scan: all baseline, but exact times still count
    jobs = _jobs()
    assert jobs["recent"]["fresh_at"] is not None and jobs["recent"]["is_seen"] == 0
    assert jobs["old"]["fresh_at"] < db.to_iso(datetime.now(timezone.utc) - timedelta(days=1))
    assert jobs["dated"]["fresh_at"] is None     # a date alone can't place it within 30 minutes


def test_page_read_differently_is_not_a_flood_of_new_jobs(site_id, monkeypatch):
    # first reads only picked up menu links; a fix makes the real postings appear all at once
    _scan_returns(monkeypatch, Job("m1", "Saved jobs", "u"), Job("m2", "Students", "u"))
    monitor.scan_site(site_id)
    time.sleep(1.1)
    _scan_returns(monkeypatch, *[Job(f"j{i}", f"Engineer {i}", f"u{i}") for i in range(20)])
    monitor.scan_site(site_id)
    jobs = _jobs()
    assert all(jobs[f"j{i}"]["fresh_at"] is None and jobs[f"j{i}"]["is_baseline"] == 1 for i in range(20))

    # after that, a genuinely new posting is fresh again
    time.sleep(1.1)
    _scan_returns(monkeypatch, *[Job(f"j{i}", f"Engineer {i}", f"u{i}") for i in range(21)])
    monitor.scan_site(site_id)
    assert _jobs()["j20"]["fresh_at"] is not None and _jobs()["j20"]["is_baseline"] == 0


def test_page_read_again_after_a_spell_of_nothing_is_not_a_flood(site_id, monkeypatch):
    # read in a browser, then unreadable for a while (browser turned off), then via its job board
    for method, jobs in [("links (rendered)", [Job(f"l{i}", f"Role {i}", f"u{i}") for i in range(5)]),
                         ("none", []),
                         ("workday", [Job(f"w{i}", f"Role {i}", f"v{i}") for i in range(10)])]:
        monkeypatch.setattr(monitor, "scan", lambda url, m=method, j=jobs: ScanResult(method=m, jobs=j))
        monitor.scan_site(site_id)
        time.sleep(1.1)
    assert all(_jobs()[f"w{i}"]["fresh_at"] is None for i in range(10))


def test_a_feed_without_posting_times_keeps_the_stored_ones(site_id, monkeypatch):
    _scan_returns(monkeypatch, Job("1", "Engineer", "u1", posted_at="2026-10-16T10:00:00Z"))
    monitor.scan_site(site_id)
    _scan_returns(monkeypatch, Job("1", "Engineer", "u1"))
    monitor.scan_site(site_id)
    assert _jobs()["1"]["posted_at"] == "2026-10-16T10:00:00Z"


def test_empty_first_scan_does_not_use_up_the_baseline(site_id, monkeypatch):
    _scan_returns(monkeypatch)
    monitor.scan_site(site_id)
    _scan_returns(monkeypatch, Job("1", "Engineer", "u1"))
    monitor.scan_site(site_id)
    assert _jobs()["1"]["is_baseline"] == 1      # the first scan that finds jobs sets the baseline


def test_failed_scan_keeps_existing_jobs(site_id, monkeypatch):
    _scan_returns(monkeypatch, Job("1", "Engineer", "u1"))
    monitor.scan_site(site_id)
    _scan_returns(monkeypatch, error="503 from https://acme.com/jobs")
    monitor.scan_site(site_id)

    assert _jobs()["1"]["is_active"] == 1
    with db.connect() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    assert site["last_status"] == "error" and "503" in site["last_error"]


def test_due_sites_respect_interval(site_id, monkeypatch):
    assert monitor.due_site_ids() == [site_id]          # never checked
    _scan_returns(monkeypatch)
    monitor.scan_site(site_id)
    assert monitor.due_site_ids() == []                 # checked just now


def test_most_overdue_sites_are_checked_first(site_id):
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        conn.execute("UPDATE sites SET interval_minutes = 5, last_checked_at = ? WHERE id = ?",
                     ((now - timedelta(minutes=6)).isoformat(), site_id))
        late = conn.execute("INSERT INTO sites (url, name, created_at, interval_minutes, last_checked_at) "
                            "VALUES ('https://b.com/jobs', 'b', ?, 2, ?)",
                            (db.now_iso(), (now - timedelta(minutes=6)).isoformat())).lastrowid
    assert monitor.due_site_ids() == [late, site_id]   # 3 intervals behind before 1.2


def test_exact_time_normalizes_formats():
    assert db.exact_time("2026-10-16T10:00:00Z") == "2026-10-16T10:00:00+00:00"
    assert db.exact_time("2026-10-16T15:30:00+05:30") == "2026-10-16T10:00:00+00:00"
    assert db.exact_time("2026-10-16T10:00:00.123456") == "2026-10-16T10:00:00+00:00"
    assert db.exact_time("2026-10-16") is None and db.exact_time(None) is None and db.exact_time("soon") is None


def test_saved_jobs_are_retagged_when_the_rules_change(site_id, monkeypatch):
    from app import classify
    _scan_returns(monkeypatch, Job("1", "Flutter Developer", "u1", "Pune, Maharashtra"))
    monitor.scan_site(site_id)
    with db.connect() as conn:   # pretend it was tagged by older rules that missed it
        conn.execute("UPDATE jobs SET is_tech = 0, tech_category = NULL, is_india = 0")
        assert db.retag(conn) == 0                      # same rules version: nothing to do
        monkeypatch.setattr(classify, "RULES_VERSION", "newer-rules")
        assert db.retag(conn) == 1
    job = _jobs()["1"]
    assert (job["is_tech"], job["tech_category"], job["is_india"]) == (1, "Software Engineering", 1)


def test_a_hung_check_is_abandoned_and_the_others_keep_going(site_id, monkeypatch):
    import asyncio
    import threading
    with db.connect() as conn:
        other = conn.execute("INSERT INTO sites (url, name, created_at) VALUES ('https://b.com/jobs', 'b', ?)",
                             (db.now_iso(),)).lastrowid
    release = threading.Event()

    def fake_scan(url):
        if "acme" in url:
            release.wait(30)   # never answers (until the test ends)
        return ScanResult(method="test", jobs=[Job("1", "Engineer", "u1")])

    monkeypatch.setattr(monitor, "scan", fake_scan)
    monkeypatch.setattr(monitor, "SCAN_TIMEOUT", 1)
    monkeypatch.setattr(monitor, "MAX_PARALLEL_SCANS", 1)   # the hung check holds the only slot

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_forever(stop))
        await asyncio.sleep(2.5)
        stop.set()
        await task

    asyncio.run(run())
    with db.connect() as conn:
        rows = {r["id"]: r for r in conn.execute("SELECT id, last_status, last_error FROM sites")}
    release.set()
    # let the abandoned check finish here, so it can't mark progress during a later test
    for t in [t for t in threading.enumerate() if t.name.startswith("check-")]:
        t.join(5)
    assert rows[site_id]["last_status"] == "error" and "didn't answer" in rows[site_id]["last_error"]
    assert rows[other]["last_status"] == "ok"   # checked once the hung one was given up on


def test_health_fails_when_the_checker_stalls(monkeypatch):
    import time as _time
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(monitor, "_last_progress", _time.monotonic() - monitor.STALL_SECONDS - 5)
    r = TestClient(main.app).get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "stalled"


def _pace_of(site_id):
    with db.connect() as conn:
        r = conn.execute("SELECT interval_minutes, effective_interval, error_streak FROM sites WHERE id = ?", (site_id,)).fetchone()
    return r["effective_interval"], r["error_streak"]


def test_companies_are_checked_as_often_as_they_are_worth(site_id, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE sites SET interval_minutes = 2 WHERE id = ?", (site_id,))
    _scan_returns(monkeypatch, Job("1", "Account Executive", "u1", location="New York"))
    monitor.scan_site(site_id)
    assert _pace_of(site_id) == (monitor.COLD_MINUTES, 0)          # nothing in India: hourly

    _scan_returns(monkeypatch, Job("2", "Account Executive", "u2", location="Mumbai, India"))
    monitor.scan_site(site_id)
    assert _pace_of(site_id)[0] == monitor.COLD_MINUTES               # only sales in India: not tech hiring

    _scan_returns(monkeypatch, Job("2", "Account Executive", "u2", location="Mumbai, India"),
                  Job("3", "Backend Engineer", "u3", location="Bengaluru, India"))
    monitor.scan_site(site_id)
    assert _pace_of(site_id)[0] == 2                                  # new tech job in India: as often as it can

    _scan_returns(monkeypatch, Job("2", "Account Executive", "u2", location="Mumbai, India"),
                  Job("3", "Backend Engineer", "u3", location="Bengaluru, India"),
                  Job("4", "Data Engineer", "u4", location="Pune, India", posted_at=_minutes_ago(60 * 24 * 30)))
    with db.connect() as conn:                                        # …the new one was a month ago now
        conn.execute("UPDATE jobs SET fresh_at = ? WHERE external_id = '3'", (_minutes_ago(60 * 24 * 30),))
    monitor.scan_site(site_id)
    assert _pace_of(site_id)[0] == monitor.WARM_MINUTES               # still open, nothing new lately
    _scan_returns(monkeypatch, Job("2", "Account Executive", "u2", location="Mumbai, India"),
                  Job("3", "Backend Engineer", "u3", location="Bengaluru, India"))
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET fresh_at = ? WHERE external_id = '3'", (_minutes_ago(5),))

    _scan_returns(monkeypatch, Job("2", "Account Executive", "u2", location="Mumbai, India"))
    monitor.scan_site(site_id)
    assert _pace_of(site_id)[0] == 2                                  # it closed, but was posted lately: keep watching

    with db.connect() as conn:                                        # …two weeks on, no tech hiring in India
        conn.execute("UPDATE jobs SET fresh_at = ?", (_minutes_ago(60 * 24 * 15),))
    monitor.scan_site(site_id)
    assert _pace_of(site_id)[0] == monitor.COLD_MINUTES


def test_a_failing_company_is_checked_less_and_less_until_it_works(site_id, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE sites SET interval_minutes = 5 WHERE id = ?", (site_id,))
    _scan_returns(monkeypatch, error="site down")
    seen = []
    for _ in range(8):
        monitor.scan_site(site_id)
        seen.append(_pace_of(site_id)[0])
    assert seen == [10, 20, 40, 80, 160, 320, 360, 360]
    _scan_returns(monkeypatch, Job("1", "Backend Engineer", "u1", location="Pune, India", posted_at=_minutes_ago(30)))
    monitor.scan_site(site_id)
    assert _pace_of(site_id) == (5, 0)                                # works again, and hiring: back to its interval


def test_due_companies_use_their_pace_and_the_backlog_is_reported(site_id, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE sites SET interval_minutes = 2, effective_interval = 60, last_checked_at = ? WHERE id = ?",
                     (db.to_iso(datetime.now(timezone.utc) - timedelta(minutes=30)), site_id))
    assert monitor.due_site_ids() == []                               # 30 min ago, checked hourly: not yet
    with db.connect() as conn:
        conn.execute("UPDATE sites SET last_checked_at = ? WHERE id = ?",
                     (db.to_iso(datetime.now(timezone.utc) - timedelta(minutes=90)), site_id))
    assert monitor.due_site_ids() == [site_id]
    status = monitor.checker_status()
    assert (status["companies"], status["overdue"]) == (1, 1) and 29 <= status["most_overdue_minutes"] <= 31
