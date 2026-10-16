from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, main, monitor
from app.scraper import Job, ScanResult


def _minutes_ago(m: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat()


@pytest.fixture
def listing(tmp_path, monkeypatch):
    """What the fake careers page currently lists; tests append to it between scans."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "api.db"))
    jobs = [
        Job("1", "Backend Engineer", "https://x/1", "Bengaluru", posted_at=_minutes_ago(10)),
        Job("2", "Data Analyst", "https://x/2", "Remote", posted_at=_minutes_ago(3 * 60)),
        Job("3", "Designer", "https://x/3", "Pune"),            # no posting time on the site
    ]
    monkeypatch.setattr(monitor, "scan", lambda url: ScanResult(method="greenhouse", jobs=list(jobs)))
    return jobs


@pytest.fixture
def client(listing):
    with TestClient(main.app) as c:
        yield c


def test_only_jobs_posted_inside_the_window_are_returned(client, listing):
    res = client.post("/api/sites", json={"url": "razorpay.com/jobs"})
    assert res.status_code == 201
    assert (res.json()["url"], res.json()["name"], res.json()["interval_minutes"]) == (
        "https://razorpay.com/jobs", "razorpay.com", 1)

    # first scan ran in the background: of the jobs already listed, only the one the
    # site says was posted 10 minutes ago is inside the 30-minute window
    ALL = {"country": "all", "role": "all"}
    jobs = client.get("/api/jobs", params={"posted_within": 30, **ALL}).json()
    assert [(j["title"], j["posted_at_source"]) for j in jobs] == [("Backend Engineer", "site")]
    assert [j["title"] for j in client.get("/api/jobs", params={"posted_within": 240, **ALL}).json()] == [
        "Backend Engineer", "Data Analyst"]
    site = client.get("/api/sites", params={"posted_within": 30, **ALL}).json()[0]
    assert (site["active_jobs"], site["fresh_jobs"], site["new_jobs"]) == (3, 1, 1)

    # a posting that appears later, with no time from the site, is fresh from when it was found
    listing.append(Job("4", "ML Engineer", "https://x/4", "Remote"))
    site_id = site["id"]
    client.post(f"/api/sites/{site_id}/scan")
    jobs = client.get("/api/jobs", params=ALL).json()           # default window is 30 minutes
    assert {(j["title"], j["posted_at_source"]) for j in jobs} == {
        ("Backend Engineer", "site"), ("ML Engineer", "found")}
    assert [j["title"] for j in client.get("/api/jobs", params={"q": "ml", **ALL}).json()] == ["ML Engineer"]


def test_seen_and_mark_all_seen(client):
    client.post("/api/sites", json={"url": "https://acme.com/careers"})
    [job] = client.get("/api/jobs").json()
    assert job["is_seen"] == 0
    assert client.post(f"/api/jobs/{job['id']}/seen").status_code == 200
    assert client.get("/api/jobs").json()[0]["is_seen"] == 1
    client.post("/api/jobs/mark-seen", json={})
    assert all(j["is_seen"] == 1 for j in client.get("/api/jobs", params={"posted_within": 43200}).json())


def test_company_on_hosted_board_is_named_after_the_company(client):
    site = client.post("/api/sites", json={"url": "https://jobs.lever.co/acme"}).json()
    assert site["name"] == "acme"


def test_invalid_and_duplicate_urls_are_rejected(client):
    assert client.post("/api/sites", json={"url": "not a url"}).status_code == 422
    client.post("/api/sites", json={"url": "https://acme.com/careers"})
    assert client.post("/api/sites", json={"url": "https://acme.com/careers"}).status_code == 409


def test_update_scan_and_remove_company(client):
    site_id = client.post("/api/sites", json={"url": "https://acme.com/careers"}).json()["id"]
    assert client.patch(f"/api/sites/{site_id}", json={"interval_minutes": 5}).json()["interval_minutes"] == 5
    assert client.patch(f"/api/sites/{site_id}", json={"interval_minutes": 0}).status_code == 422
    assert client.patch(f"/api/sites/{site_id}", json={"url": "jobs.lever.co/acme"}).json()["url"] == "https://jobs.lever.co/acme"
    assert client.post(f"/api/sites/{site_id}/scan").json()["last_job_count"] == 3
    assert client.delete(f"/api/sites/{site_id}").status_code == 204
    assert client.get("/api/sites").json() == []
    assert client.get("/api/jobs", params={"posted_within": 43200}).json() == []
    assert client.delete(f"/api/sites/{site_id}").status_code == 404


def test_default_view_is_technical_jobs_in_india(client, listing):
    listing[:] = [Job(f"old{i}", f"Old job {i}", "u", "Pune") for i in range(3)]   # already listed
    site_id = client.post("/api/sites", json={"url": "https://acme.com/careers"}).json()["id"]
    listing += [                   # posted after the first check, so they count as new
        Job("a", "Backend Engineer", "u", "Bengaluru, Karnataka"),
        Job("b", "Sales Manager", "u", "Mumbai"),
        Job("c", "Data Scientist", "u", "San Francisco, CA"),
        Job("d", "Recruiter", "u", "London"),
    ]
    client.post(f"/api/sites/{site_id}/scan")

    titles = lambda **p: sorted(j["title"] for j in client.get("/api/jobs", params=p).json())
    assert titles() == ["Backend Engineer"]                                   # India + technical
    assert titles(country="all") == ["Backend Engineer", "Data Scientist"]    # technical anywhere
    assert titles(role="all") == ["Backend Engineer", "Sales Manager"]        # anything in India
    assert len(titles(country="all", role="all")) == 4
    assert client.get("/api/sites").json()[0]["fresh_jobs"] == 1
    assert client.get("/api/sites", params={"country": "all", "role": "all"}).json()[0]["fresh_jobs"] == 4

    # "Mark all as seen" on the default view leaves the hidden jobs unseen
    client.post("/api/jobs/mark-seen", json={"country": "india", "role": "tech"})
    unseen = [j["title"] for j in client.get("/api/jobs", params={"country": "all", "role": "all"}).json() if not j["is_seen"]]
    assert sorted(unseen) == ["Data Scientist", "Recruiter", "Sales Manager"]


def test_companies_page_puts_fresh_companies_first_and_searches(client, listing):
    quiet = client.post("/api/sites", json={"url": "https://aaa-quiet.com/careers", "name": "Aaa Quiet",
                                            "scan_now": False}).json()
    busy = client.post("/api/sites", json={"url": "https://zzz-busy.com/careers", "name": "Zzz Busy"}).json()
    listing.append(Job("new1", "Backend Engineer", "u", "Bengaluru"))
    client.post(f"/api/sites/{busy['id']}/scan")

    page = client.get("/api/companies", params={"limit": 1}).json()
    assert page["total"] == 2 and page["items"][0]["name"] == "Zzz Busy"      # fresh jobs first, despite A–Z
    assert page["summary"]["companies"] == 2 and page["summary"]["fresh_jobs"] >= 1
    second = client.get("/api/companies", params={"limit": 1, "offset": 1}).json()["items"]
    assert [c["name"] for c in second] == ["Aaa Quiet"]
    assert [c["name"] for c in client.get("/api/companies", params={"q": "quiet"}).json()["items"]] == ["Aaa Quiet"]
