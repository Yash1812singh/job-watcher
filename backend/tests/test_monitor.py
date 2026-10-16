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
