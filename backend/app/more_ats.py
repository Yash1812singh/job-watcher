"""Readers for more hiring tools, many of them common with Indian companies: Keka, Darwinbox,
Zoho Recruit, Freshteam, Recruitee, BambooHR, Breezy HR, Teamtailor and Jobvite.

Each reads the public list of open jobs that the company's own careers page loads, without
signing in. A board is named by a token, as for the other hiring tools in scraper.py:
the company's subdomain (Keka, Freshteam, Recruitee, BambooHR, Breezy, Teamtailor), its host
(Darwinbox: <co>.darwinbox.in), host + careers page name (Zoho Recruit: <co>.zohorecruit.in/Careers)
or its path name (Jobvite: jobs.jobvite.com/<co>).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

TOOLS = ("keka", "darwinbox", "zohorecruit", "freshteam", "recruitee", "bamboohr", "breezy", "teamtailor", "jobvite")

# <company>.<tool> addresses; group 1 is the token (the part that names the company)
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("keka", re.compile(r"\b([a-z0-9-]+)\.keka\.com/careers", re.I)),
    ("darwinbox", re.compile(r"\b([a-z0-9-]+\.darwinbox\.(?:in|com))/ms/candidate", re.I)),
    ("zohorecruit", re.compile(r"\b([a-z0-9-]+\.zohorecruit\.(?:in|com|eu))/jobs/([\w-]+)", re.I)),
    ("freshteam", re.compile(r"\b([a-z0-9-]+)\.freshteam\.com/jobs", re.I)),
    ("recruitee", re.compile(r"\b([a-z0-9-]+)\.recruitee\.com\b", re.I)),
    ("bamboohr", re.compile(r"\b([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)", re.I)),
    ("breezy", re.compile(r"\b([a-z0-9-]+)\.breezy\.hr\b", re.I)),
    ("teamtailor", re.compile(r"\b([a-z0-9-]+)\.teamtailor\.com\b", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([\w-]+)", re.I)),
]
# the tools' own subdomains, never a company
NOT_A_COMPANY = {"www", "app", "api", "cdn", "static", "assets", "help", "support", "status", "blog", "docs",
                 "careers", "career", "jobs", "login", "auth", "mail", "admin", "my", "go", "demo", "signup",
                 "developers", "partners", "community", "marketplace", "embed", "widget", "media", "images",
                 "search", "app-eu", "eu", "us", "in"}

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def detect(text: str) -> tuple[str, str] | None:
    for name, pattern in PATTERNS:
        for m in pattern.finditer(text):
            company = m.group(1).split(".")[0].lower()
            if company in NOT_A_COMPANY:
                continue
            if name == "zohorecruit":
                return name, f"{m.group(1).lower()}/{m.group(2)}"
            return name, m.group(1).lower()
    return None


def board_url(name: str, token: str) -> str:
    return {
        "keka": f"https://{token}.keka.com/careers/",
        "darwinbox": f"https://{token}/ms/candidate/careers",
        "zohorecruit": "https://{0}/jobs/{1}".format(*token.split("/", 1)),
        "freshteam": f"https://{token}.freshteam.com/jobs",
        "recruitee": f"https://{token}.recruitee.com/",
        "bamboohr": f"https://{token}.bamboohr.com/careers",
        "breezy": f"https://{token}.breezy.hr/",
        "teamtailor": f"https://{token}.teamtailor.com/jobs",
        "jobvite": f"https://jobs.jobvite.com/{token}",
    }[name]


def _json(client: httpx.Client, url: str, **kw) -> object:
    resp = client.get(url, headers={"Accept": "application/json", **kw.pop("headers", {})}, **kw)
    resp.raise_for_status()
    if "json" not in resp.headers.get("content-type", ""):
        raise ValueError(f"not a job list ({url} answered with a page, not data): is the company still on this hiring tool?")
    return resp.json()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _date(value: str | None, fmt: str) -> str | None:
    """A date-only posting date as YYYY-MM-DD (not exact enough for "posted N minutes ago")."""
    try:
        return datetime.strptime(value.strip(), fmt).date().isoformat() if value else None
    except ValueError:
        return None


def fetch(client: httpx.Client, name: str, token: str) -> list:
    from .scraper import Job, place  # scraper imports this module

    if name == "keka":
        base = f"https://{token}.keka.com/careers"
        page = client.get(base + "/")
        if "TenantNotFound" in str(page.url) or "/Content/403" in str(page.url):
            raise ValueError(f"{token}.keka.com has no public careers page")
        found = re.search(r"/ats/documents/([0-9a-f-]{36})/", page.text)
        if not found:
            info = _json(client, f"{base}/api/organization/default/careerportalinfo")
            found = re.search(r"/ats/documents/([0-9a-f-]{36})/", str(info))
        if not found:
            raise ValueError("couldn't find this Keka careers page's job list")
        return [
            Job(external_id=str(j["id"]), title=j["title"].strip(), url=f"{base}/jobdetails/{j['id']}",
                location=place([", ".join(p for p in (l.get("city") or l.get("name"), l.get("countryName")) if p)
                                for l in j.get("jobLocations") or []]),
                department=j.get("departmentName"), posted_at=j.get("publishedOn"))
            for j in _json(client, f"{base}/api/embedjobs/default/active/{found.group(1)}")
        ]

    if name == "darwinbox":
        # behind Cloudflare: a browser's User-Agent, and a second try after an occasional refusal
        jobs, page_no = [], 1
        while page_no <= 20:
            for attempt in range(3):
                resp = client.get(f"https://{token}/ms/candidateapi/job", params={"page": page_no, "limit": 50},
                                  headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
                if resp.status_code != 403:
                    break
                time.sleep(2 + 2 * attempt)
            resp.raise_for_status()
            batch = (resp.json().get("message") or {}).get("jobs") or []
            for j in batch:
                jobs.append(Job(external_id=str(j["id"]), title=j["title"].strip(),
                                url=f"https://{token}/ms/candidate/careers/{j['id']}",
                                location=place(j.get("tool_tip_locations") or [j.get("officelocation_show_arr")]),
                                department=j.get("department") or None, posted_at=j.get("created_on")))
            if len(batch) < 50:
                break
            page_no += 1
        return jobs

    if name == "zohorecruit":
        host, page_name = token.split("/", 1)
        data = _json(client, f"https://{host}/recruit/v2/public/Job_Openings",
                     params={"pagename": page_name, "source": "CareerSite"})
        return [
            Job(external_id=str(j["id"]), title=(j.get("Job_Opening_Name") or j.get("Posting_Title") or "").strip(),
                url=j.get("$url") or f"https://{host}/jobs/{page_name}/{j['id']}",
                location=place([", ".join(p for p in (j.get("City"), j.get("State"), j.get("Country")) if p)],
                               remote=j.get("Remote_Job") in ("Yes", True)),
                posted_at=_date(j.get("Date_Opened"), "%m/%d/%Y"))
            for j in data.get("data") or []
            if j.get("Publish", True) and not j.get("Is_Locked") and (j.get("Job_Opening_Name") or j.get("Posting_Title"))
        ]

    if name == "freshteam":
        data = _json(client, f"https://{token}.freshteam.com/hire/widgets/jobs.json")
        branches = {b["id"]: b for b in data.get("branches") or []}
        roles = {r["id"]: r.get("name") for r in data.get("job_roles") or []}
        jobs = []
        for j in data.get("jobs") or []:
            b = branches.get(j.get("branch_id")) or {}
            jobs.append(Job(external_id=str(j["id"]), title=j["title"].strip(),
                            url=f"https://{token}.freshteam.com/jobs/{j.get('unique_id') or j['id']}",
                            location=place([b.get("location") or ", ".join(p for p in (b.get("city"), b.get("state")) if p)],
                                           "India" if b.get("country_code") == "IN" else None, bool(j.get("remote"))),
                            department=roles.get(j.get("job_role_id")), posted_at=j.get("created_at")))
        return jobs

    if name == "recruitee":
        data = _json(client, f"https://{token}.recruitee.com/api/offers/")
        jobs = []
        for j in data.get("offers") or []:
            posted = j.get("published_at")
            jobs.append(Job(external_id=str(j["id"]), title=j["title"].strip(),
                            url=j.get("careers_url") or f"https://{token}.recruitee.com/o/{j.get('slug')}",
                            location=place([j.get("location")], "India" if j.get("country_code") == "IN" else None, bool(j.get("remote"))),
                            department=j.get("department"),
                            posted_at=posted.replace(" UTC", "Z").replace(" ", "T") if posted else None))
        return jobs

    if name == "bamboohr":
        data = _json(client, f"https://{token}.bamboohr.com/careers/list")
        jobs = []
        for j in data.get("result") or []:
            loc = j.get("location") or {}
            ats_loc = j.get("atsLocation") or {}
            jobs.append(Job(external_id=str(j["id"]), title=(j.get("jobOpeningName") or "").strip(),
                            url=f"https://{token}.bamboohr.com/careers/{j['id']}",
                            location=place([", ".join(p for p in (loc.get("city"), loc.get("state")) if p),
                                            ", ".join(p for p in (ats_loc.get("city"), ats_loc.get("state")) if p)],
                                           ats_loc.get("country"), bool(j.get("isRemote"))),
                            department=j.get("departmentLabel")))
        return jobs

    if name == "breezy":
        data = _json(client, f"https://{token}.breezy.hr/json")
        jobs = []
        for j in data:
            loc = j.get("location") or {}
            places = [l.get("name") for l in j.get("locations") or []] or [loc.get("name")]
            jobs.append(Job(external_id=str(j["id"]), title=j["name"].strip(),
                            url=j.get("url") or f"https://{token}.breezy.hr/p/{j.get('friendly_id')}",
                            location=place(places, (loc.get("country") or {}).get("name"), bool(loc.get("is_remote"))),
                            department=(j.get("department") or None) if isinstance(j.get("department"), str) else None,
                            posted_at=j.get("published_date")))
        return jobs

    if name == "teamtailor":
        resp = client.get(f"https://{token}.teamtailor.com/jobs.rss")
        resp.raise_for_status()
        ns = {"tt": "https://teamtailor.com/locations"}
        jobs = []
        for item in ET.fromstring(resp.content).iter("item"):
            link = (item.findtext("link") or "").strip()
            places = [", ".join(p for p in (l.findtext("tt:city", namespaces=ns), l.findtext("tt:country", namespaces=ns)) if p)
                      or (l.findtext("tt:name", namespaces=ns) or "") for l in item.iter("{https://teamtailor.com/locations}location")]
            posted = item.findtext("pubDate")
            try:
                posted = _iso(parsedate_to_datetime(posted)) if posted else None
            except (TypeError, ValueError):
                posted = None
            jobs.append(Job(external_id=(item.findtext("guid") or link).strip(), title=(item.findtext("title") or "").strip(),
                            url=link, location=place(places, remote=(item.findtext("remoteStatus") or "") == "fully"),
                            department=item.findtext("tt:department", namespaces=ns) or None, posted_at=posted))
        return [j for j in jobs if j.title and j.url]

    if name == "jobvite":
        page = client.get(f"https://jobs.jobvite.com/{token}/jobs")
        found = re.search(r"companyEId:\s*'([^']+)'", page.text)
        if not found:
            raise ValueError(f"jobs.jobvite.com/{token} isn't a Jobvite careers page")
        resp = client.get("https://app.jobvite.com/CompanyJobs/Xml.aspx", params={"c": found.group(1)})
        resp.raise_for_status()
        # one row per job and place: group them (and never keep the hiring manager's name it also lists)
        by_id: dict[str, dict] = {}
        for row in ET.fromstring(resp.content).iter("job"):
            base_id = (row.findtext("id") or "").split("-")[0]
            if not base_id:
                continue
            j = by_id.setdefault(base_id, {"title": (row.findtext("title") or "").strip(), "places": [],
                                           "department": row.findtext("category"), "date": row.findtext("date")})
            j["places"].append(row.findtext("location"))
        return [Job(external_id=i, title=j["title"], url=f"https://jobs.jobvite.com/{token}/job/{i}",
                    location=place(j["places"]), department=j["department"] or None,
                    posted_at=_date(j["date"], "%m/%d/%Y"))
                for i, j in by_id.items() if j["title"]]

    raise ValueError(f"unknown hiring tool {name}")
