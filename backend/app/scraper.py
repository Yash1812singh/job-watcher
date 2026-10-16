"""Find job postings on a company's careers page.

The user adds a company's own careers URL. Strategies, most reliable first:
  1. The company's hosted job board. Many careers pages load their list from a hiring
     system (Greenhouse, Lever, Ashby, Workable, SmartRecruiters); when the page embeds or
     redirects to one, read that company's board from its public JSON feed — complete and
     structured, and still only that company's jobs.
  2. schema.org JobPosting data (JSON-LD) embedded in the page.
  3. Links that look like job detail pages (e.g. /careers/senior-engineer). A best guess.

If the plain HTML yields nothing (the page builds its list with JavaScript), the page is
opened in headless Chrome and strategies 1-3 run again on the rendered page.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; JobWatcher/0.1; personal job tracker)"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)  # some boards list thousands of jobs


class ScanError(Exception):
    """The site couldn't be read (network error, HTTP error, bad URL)."""


@dataclass
class Job:
    external_id: str          # stable key for this posting within the site
    title: str
    url: str
    location: str | None = None
    department: str | None = None
    posted_at: str | None = None


@dataclass
class ScanResult:
    method: str
    jobs: list[Job] = field(default_factory=list)
    note: str | None = None   # shown to the user, e.g. why nothing was found
    weak: bool = False        # only general links (no IDs / detail pages) were found; likely menus


# ── ATS detection ────────────────────────────────────────────────────────────

ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)", re.I)),
    # Workday (NVIDIA, Salesforce, Adobe, Walmart, …): <tenant>.wd<N>.myworkdayjobs.com/[en-US/]<site>
    ("workday", re.compile(
        r"(?P<tenant>[\w-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[\w-]+)", re.I)),
    # the same boards also live at wd<N>.myworkdaysite.com/recruiting/<tenant>/<site> (Snap, Wells Fargo)
    ("workday", re.compile(
        r"(?P<wd>wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/(?P<tenant>[\w-]+)/(?P<site>[\w-]+)", re.I)),
]
_NOT_A_BOARD = {"embed", "v1", "api", "j", "jobs", "widget", "static", "wday"}
# A Workday site name is chosen by the company and is often just "jobs"; only the API path is not a board.
_NOT_A_WORKDAY_SITE = {"wday"}

# How many of the newest postings to read from very large boards each check. They list
# newest first, so this is where new jobs appear; older ones only matter to "listed" counts.
NEWEST_LIMIT = int(os.environ.get("JOB_WATCHER_NEWEST_LIMIT", "200"))


def detect_ats(text: str) -> tuple[str, str] | None:
    """Return (ats, board token) for the first ATS reference in a URL or page source."""
    for name, pattern in ATS_PATTERNS:
        for match in pattern.finditer(text):
            if name == "workday":   # multi-part board, joined into one token: tenant/wdN/site
                if match["site"].lower() not in _NOT_A_WORKDAY_SITE:
                    return name, f"{match['tenant']}/{match['wd']}/{match['site']}"
                continue
            token = match.group(1).strip(".")
            if token and token.lower() not in _NOT_A_BOARD:
                return name, token
    return None


def _get_json(client: httpx.Client, url: str) -> object:
    resp = client.get(url, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def fetch_ats_jobs(client: httpx.Client, ats: str, token: str, page_url: str = "") -> list[Job]:
    if ats == "greenhouse":
        data = _get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        return [
            Job(
                external_id=str(j["id"]),
                title=j["title"].strip(),
                url=j.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{j['id']}",
                location=(j.get("location") or {}).get("name"),
                posted_at=j.get("first_published"),  # updated_at changes on every edit, so it isn't a posting time
            )
            for j in data.get("jobs", [])
        ]

    if ats == "lever":
        host = "api.eu.lever.co" if ".eu.lever.co" in page_url else "api.lever.co"
        data = _get_json(client, f"https://{host}/v0/postings/{token}?mode=json")
        jobs = []
        for j in data:
            cats = j.get("categories") or {}
            created = j.get("createdAt")
            jobs.append(Job(
                external_id=j["id"],
                title=j["text"].strip(),
                url=j.get("hostedUrl") or f"https://jobs.lever.co/{token}/{j['id']}",
                location=cats.get("location"),
                department=cats.get("team") or cats.get("department"),
                posted_at=_ms_to_iso(created) if isinstance(created, (int, float)) else None,
            ))
        return jobs

    if ats == "ashby":
        data = _get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        return [
            Job(
                external_id=j["id"],
                title=j["title"].strip(),
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{token}/{j['id']}",
                location=j.get("location"),
                department=j.get("department") or j.get("team"),
                posted_at=j.get("publishedAt"),
            )
            for j in data.get("jobs", [])
            if j.get("isListed", True)
        ]

    if ats == "workable":
        data = _get_json(client, f"https://apply.workable.com/api/v1/widget/accounts/{token}")
        jobs = []
        for j in data.get("jobs", []):
            place = ", ".join(p for p in (j.get("city"), j.get("country")) if p) or None
            jobs.append(Job(
                external_id=j.get("shortcode") or j["url"],
                title=j["title"].strip(),
                url=j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{token}/j/{j.get('shortcode')}",
                location=place,
                department=j.get("department"),
                posted_at=j.get("published_on"),
            ))
        return jobs

    if ats == "smartrecruiters":
        jobs: list[Job] = []
        offset = 0
        while offset < 1000:
            data = _get_json(client, f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}")
            content = data.get("content", [])
            for j in content:
                loc = j.get("location") or {}
                jobs.append(Job(
                    external_id=str(j["id"]),
                    title=j["name"].strip(),
                    url=f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                    location=", ".join(p for p in (loc.get("city"), loc.get("country")) if p) or None,
                    department=(j.get("department") or {}).get("label"),
                    posted_at=j.get("releasedDate"),
                ))
            offset += len(content)
            if not content or offset >= data.get("totalFound", 0):
                break
        return jobs

    if ats == "workday":
        tenant, wd, site = token.split("/")
        host = f"{tenant}.{wd}.myworkdayjobs.com"
        jobs, offset = [], 0
        while offset < NEWEST_LIMIT:
            resp = client.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                               json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": {}},
                               headers={"Accept": "application/json"})
            resp.raise_for_status()
            postings = resp.json().get("jobPostings", [])
            for j in postings:
                if not j.get("title") or not j.get("externalPath"):
                    continue  # occasionally a placeholder row without a title
                path = j["externalPath"]
                req_id = (j.get("bulletFields") or [None])[0]
                jobs.append(Job(
                    external_id=req_id or path,
                    title=j["title"].strip(),
                    url=f"https://{host}/{site}{path}",
                    location=j.get("locationsText"),
                    # Workday says "Posted Today" / "Posted 3 Days Ago": old postings that reappear stay old
                    posted_at=relative_posted(j.get("postedOn")),
                ))
            offset += len(postings)
            if len(postings) < 20:
                break
        return jobs

    raise ValueError(f"unknown ATS {ats}")


# ── Company-specific job feeds ───────────────────────────────────────────────

def detect_feed(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.endswith("amazon.jobs"):
        return "amazon"
    if host.endswith("eightfold.ai") or host == "explore.jobs.netflix.net":
        return "eightfold"
    if host == "higher.gs.com":
        return "goldman"
    if host in ("apply.careers.microsoft.com", "jobs.careers.microsoft.com"):
        return "microsoft"
    return None


def fetch_amazon_jobs(client: httpx.Client, url: str) -> list[Job]:
    """amazon.jobs search feed, newest first. Keeps any filters in the URL you added,
    e.g. https://www.amazon.jobs/en/search?base_query=data&country=IND."""
    params = [(k, v) for k, v in parse_qsl(urlparse(url).query) if k not in ("offset", "result_limit", "sort")]
    jobs, offset = [], 0
    while offset < NEWEST_LIMIT:
        resp = client.get("https://www.amazon.jobs/en/search.json",
                          params=params + [("sort", "recent"), ("result_limit", 100), ("offset", offset)])
        resp.raise_for_status()
        batch = resp.json().get("jobs", [])
        for j in batch:
            jobs.append(Job(
                external_id=str(j.get("id_icims") or j["job_path"]),
                title=j["title"].strip(),
                url="https://www.amazon.jobs" + j["job_path"],
                location=j.get("normalized_location") or j.get("location"),
                department=j.get("job_category"),
                posted_at=_long_date(j.get("posted_date")),
            ))
        offset += len(batch)
        if len(batch) < 100:
            break
    return jobs


def fetch_eightfold_jobs(client: httpx.Client, url: str) -> list[Job]:
    """Eightfold careers sites (e.g. Netflix), newest first."""
    parsed = urlparse(url)
    domain = dict(parse_qsl(parsed.query)).get("domain") or (
        "netflix.com" if parsed.netloc == "explore.jobs.netflix.net" else parsed.netloc.split(".")[0] + ".com")
    jobs, start = [], 0
    while start < NEWEST_LIMIT:
        resp = client.get(f"https://{parsed.netloc}/api/apply/v2/jobs",
                          params={"domain": domain, "start": start, "num": 50, "sort_by": "new"})
        resp.raise_for_status()
        batch = resp.json().get("positions", [])
        for p in batch:
            created = p.get("t_create")
            jobs.append(Job(
                external_id=str(p["id"]),
                title=p["name"].strip(),
                url=p.get("canonicalPositionUrl") or f"https://{parsed.netloc}/careers/job/{p['id']}",
                location=p.get("location"),
                department=p.get("department"),
                # t_create is the posting day, not the minute, so it's kept as a date
                posted_at=_ms_to_iso(created * 1000)[:10] if isinstance(created, (int, float)) else None,
            ))
        start += len(batch)
        if len(batch) < 50:
            break
    return jobs


def _long_date(value: str | None) -> str | None:
    """'October 16, 2026' -> '2026-10-16' (a date only, so it never counts as an exact time)."""
    from datetime import datetime
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat() if value else None
    except ValueError:
        return None


_RELATIVE = re.compile(r"posted\s*(?:on\s*)?(?:(today|just now)|(yesterday)|(\d+)\+?\s*(minute|min|hour|hr|day|week|month)s?\s*ago)", re.I)


def relative_posted(text: str | None) -> str | None:
    """'Posted 25 minutes ago' / 'Posted 3 Days Ago' / 'Posted Yesterday' -> an approximate UTC time.
    'Posted Today' says only the day, so it returns None (the time Job Watcher first sees it is used)."""
    from datetime import datetime, timedelta, timezone
    m = _RELATIVE.search(text or "")
    if not m:
        return None
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if m.group(1):
        return now.isoformat() if m.group(1).lower() == "just now" else None
    if m.group(2):
        return (now - timedelta(days=1)).isoformat()
    n, unit = int(m.group(3)), m.group(4).lower()
    delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n),
             "hr": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n),
             "month": timedelta(days=30 * n)}[unit]
    return (now - delta).isoformat()


def _ms_to_iso(ms: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()


# ── JSON-LD JobPosting ───────────────────────────────────────────────────────

def _walk_ld(node: object):
    """Yield every dict inside a JSON-LD document (handles lists, @graph, ItemList)."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_ld(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _walk_ld(node[key])


def _is_type(node: dict, wanted: str) -> bool:
    t = node.get("@type")
    return t == wanted or (isinstance(t, list) and wanted in t)


def _ld_location(node: dict) -> str | None:
    places = node.get("jobLocation")
    places = places if isinstance(places, list) else [places] if places else []
    parts = []
    for place in places:
        addr = place.get("address") if isinstance(place, dict) else None
        if isinstance(addr, dict):
            text = ", ".join(str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry")
                             if isinstance(addr.get(k), str) and addr.get(k))
            if text:
                parts.append(text)
        elif isinstance(addr, str):
            parts.append(addr)
    if not parts and str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        return "Remote"
    return "; ".join(dict.fromkeys(parts)) or None


def extract_json_ld_jobs(soup: BeautifulSoup, page_url: str) -> list[Job]:
    jobs: dict[str, Job] = {}
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_ld(data):
            if not _is_type(node, "JobPosting") or not node.get("title"):
                continue
            url = urljoin(page_url, node.get("url") or page_url)
            ident = node.get("identifier")
            ident = ident.get("value") if isinstance(ident, dict) else ident
            key = str(ident) if ident else f"{url}#{node['title']}"
            jobs[key] = Job(
                external_id=key,
                title=_clean(str(node["title"])),
                url=url,
                location=_ld_location(node),
                posted_at=node.get("datePosted"),
            )
    return list(jobs.values())


# ── Job-looking links ────────────────────────────────────────────────────────

JOB_PATH = re.compile(
    r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies)|requisitions?|roles?|job-?posting)s?/[^/?#]{3,}",
    re.I,
)
NAV_TEXT = {
    "jobs", "careers", "career", "all jobs", "view all", "view all jobs", "see all", "see all jobs",
    "open positions", "open roles", "apply", "apply now", "learn more", "read more", "search jobs",
    "job search", "join us", "life at", "benefits", "teams", "locations", "students", "internships",
    "back", "next", "previous", "home",
}


# Hiring systems that host a company's job pages on their own domain
# (e.g. acme.keka.com/careers/jobdetails/123). Links into these count as job links.
HOSTED_BOARD_DOMAINS = (
    "keka.com", "darwinbox.in", "darwinbox.com", "zohorecruit.com", "zohorecruit.in", "freshteam.com",
    "recruitee.com", "breezy.hr", "myworkdayjobs.com", "icims.com", "jobvite.com", "bamboohr.com",
    "teamtailor.com", "personio.de", "personio.com", "hire.trakstar.com", "applytojob.com",
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
)
# Path segments that mark a posting page rather than a general careers page.
# ("jobs"/"positions" are left out: whole careers sites live under /jobs/, menus included.)
DETAIL_SEGMENT = re.compile(r"^(?:listings?|details?|jobdetails?|job-?details?|requisitions?|req|postings?|vacanc(?:y|ies))$", re.I)
ID_LIKE = re.compile(r"\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_hosted_board(host: str) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in HOSTED_BOARD_DOMAINS)


def _looks_like_posting(path: str) -> bool:
    """A path with an ID or a 'details/listing/job' segment, e.g. /careers/details/27694."""
    segments = [s for s in path.split("/") if s]
    return any(ID_LIKE.search(s) for s in segments[1:]) or any(DETAIL_SEGMENT.match(s) for s in segments[1:-1])


GENERIC_LINK_TEXT = re.compile(r"^(?:learn more|read more|view( job| details| role)?|details|apply( now)?|see more|more)\b", re.I)
ARIA_PREFIX = re.compile(r"^(?:learn more about|read more about|view( details for| job)?|apply (now )?(for|to)|see)\s+", re.I)


def _card_heading(a) -> str | None:
    """Heading of the job card that contains a text-less link (e.g. an icon or 'Learn more')."""
    node = a
    for _ in range(5):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            return None
        other_postings = {x["href"] for x in node.find_all("a", href=True)
                          if x["href"] != a["href"] and ID_LIKE.search(x["href"])}
        if other_postings:  # grew past this one job's card into the list
            return None
        heading = node.find(["h2", "h3", "h4", "h5"])
        if heading:
            return _clean(heading.get_text(" "))
    return None


def _title_and_meta(a) -> tuple[str, str | None]:
    """Title from a heading inside the link, else its first text block; the rest
    (usually location) is returned separately when it's short. Links with no useful text
    fall back to their aria-label or the card's heading."""
    heading = a.find(["h1", "h2", "h3", "h4", "h5", "strong"])
    if heading:
        title = _clean(heading.get_text(" "))
        rest = _clean(a.get_text(" ").replace(heading.get_text(" "), " ", 1))
    else:
        parts = [_clean(s) for s in a.stripped_strings if _clean(s)]
        title, rest = (parts[0], " · ".join(parts[1:])) if parts else ("", "")
    if not title or GENERIC_LINK_TEXT.match(title):
        aria = _clean(a.get("aria-label") or "")
        title = ARIA_PREFIX.sub("", aria) if aria else (_card_heading(a) or title)
        rest = ""
    return title, (rest if rest and len(rest) <= 80 else None)


def _is_icon_name(title: str) -> bool:
    """Icon font names that leak into link text, e.g. 'work_outline', 'noogler_hat'."""
    return " " not in title and ("_" in title or title.islower())


def extract_link_jobs(soup: BeautifulSoup, page_url: str) -> list[Job]:
    return _link_candidates(soup, page_url)[0]


def _link_candidates(soup: BeautifulSoup, page_url: str) -> tuple[list[Job], bool]:
    """Job-like links on the page, and whether any of them clearly point at a posting."""
    page = urlparse(page_url)
    page_root = page.netloc.removeprefix("www.").split(".")[-2:]
    base = soup.find("base", href=True)
    base_url = urljoin(page_url, base["href"]) if base else page_url   # pages may set their own base
    candidates: dict[str, tuple[Job, bool]] = {}
    seen_postings: set[tuple[str, str]] = set()
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base_url, a["href"]))[0]
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or href.rstrip("/") == page_url.rstrip("/"):
            continue
        same_site = parsed.netloc.removeprefix("www.").split(".")[-2:] == page_root
        if same_site:
            # /careers/... or /jobs/... paths, or a posting page like jobs.apple.com/en-us/details/114438210/...
            segments = [s for s in parsed.path.split("/") if s]
            detail_page = (any(DETAIL_SEGMENT.match(s) for s in segments)
                           and any(ID_LIKE.search(s) for s in segments))
            if not JOB_PATH.search(parsed.path) and not detail_page:
                continue
            strong = _looks_like_posting(parsed.path) or bool(re.search(r"(?:^|&)(?:id|jobid|job_id|gh_jid|jid)=[^&]*\d", parsed.query, re.I))
        else:
            # the company's own board hosted by a hiring system: <company>.keka.com/..., jobs.lever.co/<company>/...
            if not _is_hosted_board(parsed.netloc) or len([p for p in parsed.path.split("/") if p]) < 2:
                continue
            strong = True
        title, meta = _title_and_meta(a)
        if (not (4 <= len(title) <= 140) or title.lower() in NAV_TEXT or _is_icon_name(title)
                or not re.search(r"[A-Za-z]{2}", title)):
            continue
        # One entry per posting: a card often has several links to the same job ID
        # (e.g. the title and a "Where we're hiring" link).
        posting_id = next((m.group(0) for s in parsed.path.split("/") if (m := re.search(r"\d{5,}", s))), None)
        if posting_id:
            key = (parsed.netloc, posting_id)
            if key in seen_postings:
                continue
            seen_postings.add(key)
        posted = relative_posted(meta) or relative_posted(_clean(a.parent.get_text(" ")) if a.parent else None)
        if meta:   # "India, Telangana, Hyderabad · Posted 25 minutes ago" -> just the place
            meta = re.split(r"\s*·?\s*posted\b", meta, flags=re.I)[0].strip(" ·") or None
        if href not in candidates or (strong and not candidates[href][1]):
            candidates[href] = (Job(external_id=href, title=title, url=href, location=meta, posted_at=posted), strong)
    # When the page has clear posting links, drop the vaguer ones (e.g. /careers/benefits).
    strong_jobs = [job for job, strong in candidates.values() if strong]
    if len(strong_jobs) >= 2:
        return strong_jobs, True
    return [job for job, _ in candidates.values()], False


MICROSOFT_NEWEST = 30


def fetch_microsoft_jobs(client: httpx.Client) -> list[Job]:
    """Microsoft's careers site (Eightfold "PCSX"), newest first, 10 per page. It refuses
    "compatible" user agents and allows only a few quick requests, so read just the newest
    few pages, spaced out; Microsoft posts a few dozen roles a day."""
    jobs, start = [], 0
    while start < MICROSOFT_NEWEST:
        if start:
            time.sleep(1)
        resp = client.get("https://apply.careers.microsoft.com/api/pcsx/search",
                          params={"domain": "microsoft.com", "query": "", "location": "", "start": start,
                                  "sort_by": "timestamp"},
                          headers={"User-Agent": USER_AGENT.replace("compatible; ", "")})
        if resp.status_code == 429 and jobs:
            break   # rate limited partway: the newest postings are already in
        resp.raise_for_status()
        batch = (resp.json().get("data") or {}).get("positions") or []
        for p in batch:
            posted = p.get("postedTs")
            jobs.append(Job(
                external_id=str(p.get("displayJobId") or p["id"]),
                title=p["name"].strip(),
                url=f"https://apply.careers.microsoft.com/careers/job/{p['id']}",
                location="; ".join(p.get("locations") or []) or None,
                department=p.get("department"),
                posted_at=_ms_to_iso(posted * 1000) if isinstance(posted, (int, float)) else None,
            ))
        start += len(batch)
        if not batch:
            break
    return jobs


GOLDMAN_QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId jobTitle division locations { primary city country } }
  }
}"""


def fetch_goldman_jobs(client: httpx.Client) -> list[Job]:
    """Goldman Sachs (higher.gs.com) reads its roles from this GraphQL API. It can't sort by
    date, so the whole list (a few hundred roles) is read each time."""
    jobs, page = [], 0
    while True:
        resp = client.post("https://api-higher.gs.com/gateway/api/v1/graphql",
                           headers={"Referer": "https://higher.gs.com/"},
                           json={"operationName": "GetRoles", "query": GOLDMAN_QUERY, "variables": {"searchQueryInput": {
                               "page": {"pageSize": 100, "pageNumber": page},
                               "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                               "filters": [], "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": ""}}})
        resp.raise_for_status()
        result = (resp.json().get("data") or {}).get("roleSearch") or {}
        batch = result.get("items") or []
        for r in batch:
            places = r.get("locations") or []
            place = next((loc for loc in places if loc.get("primary")), places[0] if places else {})
            jobs.append(Job(
                external_id=r["roleId"],
                title=r["jobTitle"].strip(),
                url=f"https://higher.gs.com/roles/{r['roleId'].split('_')[0]}",
                location=", ".join(v for v in (place.get("city"), place.get("country")) if v) or None,
                department=r.get("division"),
            ))
        page += 1
        if len(batch) < 100 or len(jobs) >= result.get("totalCount", 0) or page >= 20:
            break
    return jobs


# ── Rendering JavaScript pages ───────────────────────────────────────────────

RENDER_ENABLED = os.environ.get("JOB_WATCHER_RENDER", "1") != "0"
RENDER_TIMEOUT_MS = 45_000
# Each browser check starts its own Chrome; cap how many run at once so hundreds of
# companies don't open hundreds of browsers.
_render_slots = threading.BoundedSemaphore(int(os.environ.get("JOB_WATCHER_MAX_BROWSERS", "2")))
# "chrome" uses the installed Google Chrome; empty uses Playwright's own Chromium (servers, Docker).
BROWSER_CHANNEL = os.environ.get("JOB_WATCHER_BROWSER_CHANNEL", "chrome") or None


def render_page(url: str) -> tuple[str, str]:
    """Open the page in headless Chrome, let its scripts build the job list, and return
    (final url, rendered html). Uses the installed Google Chrome, so no browser download."""
    with _render_slots:
        return _render(url)


def _render(url: str) -> tuple[str, str]:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    try:
        with sync_playwright() as p:
            # small shared memory in containers crashes Chrome unless it uses /tmp instead
            browser = p.chromium.launch(channel=BROWSER_CHANNEL, headless=True,
                                        args=["--disable-dev-shm-usage", "--disable-gpu"])
            try:
                page = browser.new_page(user_agent=USER_AGENT.replace("compatible; ", ""))
                # job lists don't need pictures, fonts or video; skipping them saves memory and time
                page.route("**/*", lambda route: route.abort()
                           if route.request.resource_type in ("image", "media", "font") else route.continue_())
                # Many careers pages load their jobs from a data file (e.g. careers.swiggy.in/api/jobs.json)
                # and build cards that aren't plain links. Keep those JSON responses: they often
                # point at the company's real job board.
                data_files: list[str] = []

                def keep_json(response) -> None:
                    try:
                        if "json" in (response.headers.get("content-type") or "") and sum(map(len, data_files)) < 5_000_000:
                            data_files.append(response.text())
                    except PlaywrightError:
                        pass

                page.on("response", keep_json)
                page.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT_MS)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightError:
                    pass  # some sites never go quiet (analytics, chat widgets); use what's loaded
                # scroll so lazily loaded listings appear
                for _ in range(4):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(700)
                html = page.content()
                if data_files:
                    # appended as an inert block so job-board detection sees it; link/JSON-LD parsing ignores it
                    html += "\n<script type=\"application/x-loaded-data\">\n" + "\n".join(data_files).replace("</", "<\\/") + "\n</script>"
                return page.url, html
            finally:
                browser.close()
    except PlaywrightError as e:
        raise ScanError(f"Couldn't open {url} in the browser: {str(e).splitlines()[0]}") from e


# ── Entry point ──────────────────────────────────────────────────────────────

def _extract(client: httpx.Client, html: str, page_url: str, suffix: str = "") -> ScanResult | None:
    """Try every in-page strategy on one HTML document. None when nothing was found."""
    ats = detect_ats(html)
    if ats:
        try:
            jobs = fetch_ats_jobs(client, *ats, page_url=page_url)
            if jobs:
                return ScanResult(method=ats[0] + suffix, jobs=jobs)
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # a stray mention that isn't the company's real board; keep looking

    soup = BeautifulSoup(html, "html.parser")
    jobs = extract_json_ld_jobs(soup, page_url)
    if jobs:
        return ScanResult(method="json-ld" + suffix, jobs=jobs)

    jobs, clear = _link_candidates(soup, page_url)
    if jobs:
        note = ("Read from job links on the page. This site doesn't publish posting times, so a job's "
                "time is when Job Watcher first saw it (within a check of it going live)."
                if clear else
                "Only general links were found (they may be menu items, not jobs). "
                "Try the page that lists the openings, or the company's job board.")
        return ScanResult(method="links" + suffix, jobs=jobs, note=note, weak=not clear)
    return None


def scan(url: str, client: httpx.Client | None = None, render: bool | None = None) -> ScanResult:
    """Read one company careers page and return the jobs it lists.
    Raises ScanError if the page can't be read."""
    render = RENDER_ENABLED if render is None else render
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        feed = detect_feed(url)
        if feed == "amazon":
            return ScanResult(method="amazon", jobs=fetch_amazon_jobs(client, url),
                              note=f"Checks Amazon's newest {NEWEST_LIMIT} postings each time.")
        if feed == "eightfold":
            return ScanResult(method="eightfold", jobs=fetch_eightfold_jobs(client, url))
        if feed == "goldman":
            return ScanResult(method="goldman", jobs=fetch_goldman_jobs(client))
        if feed == "microsoft":
            return ScanResult(method="microsoft", jobs=fetch_microsoft_jobs(client),
                              note=f"Checks Microsoft's newest {MICROSOFT_NEWEST} postings each time.")

        ats = detect_ats(url)
        if ats:
            note = f"Checks the newest {NEWEST_LIMIT} postings each time." if ats[0] == "workday" else None
            return ScanResult(method=ats[0], jobs=fetch_ats_jobs(client, *ats, page_url=url), note=note)

        static_error: ScanError | None = None
        weak_result: ScanResult | None = None   # only menu-like links in the plain HTML
        try:
            resp = client.get(url)
            resp.raise_for_status()
            final_url = str(resp.url)
            ats = detect_ats(final_url)
            if ats:  # the careers page redirected straight to the company's hosted board
                return ScanResult(method=ats[0], jobs=fetch_ats_jobs(client, *ats, page_url=final_url))
            result = _extract(client, resp.text, final_url)
            if result and not (result.weak and render):
                return result
            weak_result = result
        except httpx.HTTPStatusError as e:
            # some sites block plain HTTP clients (403) but load fine in a real browser
            static_error = ScanError(f"{e.response.status_code} from {e.request.url}")
            if not render or e.response.status_code not in (401, 403, 406, 429, 503):
                raise static_error from e

        if render:
            final_url, html = render_page(url)
            result = _extract(client, html, final_url, suffix=" (rendered)")
            if result and not result.weak:
                return result
            if result or weak_result:   # the browser didn't find anything better
                return weak_result or result
            return ScanResult(method="none", jobs=[], note=(
                "No job postings were recognised, even after loading the page in a browser. "
                "Try the page that lists the openings (often /careers/jobs or a 'See open roles' link)."))

        if static_error:
            raise static_error
        return ScanResult(method="none", jobs=[], note=(
            "No job postings were recognised. The page may load its jobs with JavaScript; "
            "turn on browser rendering (JOB_WATCHER_RENDER=1)."))
    except httpx.HTTPStatusError as e:
        raise ScanError(f"{e.response.status_code} from {e.request.url}") from e
    except httpx.HTTPError as e:
        raise ScanError(f"Couldn't reach {url}: {e.__class__.__name__}: {e}") from e
    finally:
        if own_client:
            client.close()
