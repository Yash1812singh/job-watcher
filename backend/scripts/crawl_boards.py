"""Collect job-board addresses (Greenhouse, Lever, Ashby, Workday) from the Common Crawl web
archive index, so companies can be found without knowing their names first.

    cd backend && .venv/bin/python scripts/crawl_boards.py --crawls 3

Writes seed/crawl-boards.txt: one board URL per line, de-duplicated. Use
scripts/find_india_boards.py next to keep the boards that have jobs in India.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper import detect_ats  # noqa: E402

PATTERNS = ["boards.greenhouse.io/*", "job-boards.greenhouse.io/*", "jobs.lever.co/*",
            "jobs.ashbyhq.com/*", "*.myworkdayjobs.com"]
# Hiring tools many Indian companies use (and two whose readers exist but weren't searched for)
MORE_PATTERNS = ["apply.workable.com/*", "jobs.smartrecruiters.com/*", "careers.smartrecruiters.com/*",
                 "*.keka.com", "*.darwinbox.in", "*.darwinbox.com", "*.zohorecruit.in", "*.zohorecruit.com",
                 "*.freshteam.com", "*.recruitee.com", "*.bamboohr.com", "*.breezy.hr", "*.teamtailor.com",
                 "jobs.jobvite.com/*"]
# a company's board on a hiring tool where the company is the subdomain: <company>.<tool>
SUBDOMAIN_BOARDS = {
    "keka.com": "https://{c}.keka.com/careers/",
    "darwinbox.in": "https://{c}.darwinbox.in/ms/candidate/careers",
    "darwinbox.com": "https://{c}.darwinbox.com/ms/candidate/careers",
    "zohorecruit.in": "https://{c}.zohorecruit.in/jobs/Careers",
    "zohorecruit.com": "https://{c}.zohorecruit.com/jobs/Careers",
    "freshteam.com": "https://{c}.freshteam.com/jobs",
    "recruitee.com": "https://{c}.recruitee.com/",
    "bamboohr.com": "https://{c}.bamboohr.com/careers",
    "breezy.hr": "https://{c}.breezy.hr/",
    "teamtailor.com": "https://{c}.teamtailor.com/jobs",
}
# subdomains that are the tool's own pages, not a company
NOT_A_COMPANY = {"www", "app", "api", "help", "support", "status", "blog", "docs", "careers", "jobs", "login", "auth",
                 "cdn", "static", "assets", "mail", "marketplace", "partners", "developers", "community", "info", "go",
                 "signup", "demo", "my", "admin", "accounts", "learn", "academy", "resources", "events", "content"}
PATH_BOARDS = [
    (re.compile(r"apply\.workable\.com/(?!api\b|j\b)([\w-]{2,80})", re.I), "https://apply.workable.com/{c}/"),
    (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/(?!oneclick-ui\b)([A-Za-z][\w-]{1,80})", re.I), "https://jobs.smartrecruiters.com/{c}"),
    (re.compile(r"jobs\.jobvite\.com/(?!careers\b)([\w-]{2,80})", re.I), "https://jobs.jobvite.com/{c}"),
]
BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{t}",
    "lever": "https://jobs.lever.co/{t}",
    "ashby": "https://jobs.ashbyhq.com/{t}",
}


def get(client: httpx.Client, endpoint: str, **params) -> httpx.Response | None:
    for attempt in range(8):           # the index is slow and often times out (504); be patient
        try:
            r = client.get(endpoint, params=params)
            if r.status_code == 200:
                return r
            if r.status_code in (404,):
                return None
        except httpx.HTTPError:
            pass
        time.sleep(min(10 * 2 ** attempt, 300))
    return None


def board_from(url: str) -> str | None:
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    for domain, board in SUBDOMAIN_BOARDS.items():
        if host.endswith("." + domain):
            company = host[: -len(domain) - 1]
            if "." in company or company in NOT_A_COMPANY or not re.fullmatch(r"[a-z0-9-]{2,63}", company):
                return None
            return board.format(c=company)
    for pattern, board in PATH_BOARDS:
        m = pattern.search(url)
        if m:
            return board.format(c=m.group(1))
    ats = detect_ats(url)
    if not ats:
        return None
    name, token = ats
    if name == "workday":
        tenant, wd, site = token.split("/")
        if site.lower() in ("wday", "en-us", "job", "jobs"):
            return None
        return f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    if name in BOARD_URL and re.fullmatch(r"[\w.-]{2,80}", token):
        return BOARD_URL[name].format(t=token.lower())
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crawls", type=int, default=3, help="how many recent crawls to read")
    parser.add_argument("--out", default=str(ROOT / "seed" / "crawl-boards.txt"))
    parser.add_argument("--more", action="store_true", help="search the other hiring tools (Keka, Darwinbox, Zoho Recruit…)")
    args = parser.parse_args()
    patterns = MORE_PATTERNS if args.more else PATTERNS

    boards: set[str] = set()
    with httpx.Client(timeout=180, headers={"User-Agent": "JobWatcher/0.1 (personal job tracker)"}) as client:
        crawls = client.get("https://index.commoncrawl.org/collinfo.json").json()[: args.crawls]
        for crawl in crawls:
            api = crawl["cdx-api"]
            for pattern in patterns:
                info = get(client, api, url=pattern, output="json", showNumPages="true")
                pages = json.loads(info.text)["pages"] if info else 0
                before = len(boards)
                for page in range(pages):
                    r = get(client, api, url=pattern, output="json", fl="url", page=page)
                    if not r:
                        continue
                    for line in r.text.splitlines():
                        try:
                            board = board_from(json.loads(line)["url"])
                        except (ValueError, KeyError):
                            continue
                        if board:
                            boards.add(board)
                time.sleep(2)  # be gentle with a free, busy service
                print(f"{crawl['id']} {pattern:<28} {pages} pages, +{len(boards) - before} boards "
                      f"(total {len(boards)})", flush=True)

    out = Path(args.out)
    out.write_text("".join(f"{b}\n" for b in sorted(boards)))
    print(f"{len(boards)} distinct job boards → {out}")


if __name__ == "__main__":
    main()
