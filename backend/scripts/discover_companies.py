"""Find job boards for companies that are hiring, using the open Y Combinator company dataset
(https://github.com/yc-oss/api), and write them to seed/discovered.txt.

    cd backend && .venv/bin/python scripts/discover_companies.py            # hiring companies
    cd backend && .venv/bin/python scripts/discover_companies.py --more --out seed/discovered-more.txt
    cd backend && .venv/bin/python scripts/seed_companies.py --file seed/discovered.txt --interval 5

For each company it tries likely board names (its YC slug, its name, its website's domain) on
Ashby, Greenhouse and Lever, and keeps the first board that actually lists jobs. Companies
already in seed/candidates.txt or being watched are skipped.
"""
from __future__ import annotations

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATASET = "https://yc-oss.github.io/api/companies/{which}.json"
API = "http://127.0.0.1:8090/api"

BOARDS = [
    # (service, board URL to add, JSON feed to probe, how to count jobs in the feed)
    ("ashby", "https://jobs.ashbyhq.com/{t}", "https://api.ashbyhq.com/posting-api/job-board/{t}", lambda d: len(d.get("jobs", []))),
    ("greenhouse", "https://boards.greenhouse.io/{t}", "https://boards-api.greenhouse.io/v1/boards/{t}/jobs", lambda d: len(d.get("jobs", []))),
    ("lever", "https://jobs.lever.co/{t}", "https://api.lever.co/v0/postings/{t}?mode=json&limit=1", lambda d: len(d) if isinstance(d, list) else 0),
    ("workable", "https://apply.workable.com/{t}/", "https://apply.workable.com/api/v1/widget/accounts/{t}",
     lambda d: len(d.get("jobs", []))),
    ("smartrecruiters", "https://careers.smartrecruiters.com/{t}", "https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=1",
     lambda d: d.get("totalFound", 0)),
]


def board_names(company: dict) -> list[str]:
    """Likely job-board names for a company, most likely first."""
    names = []
    m = re.search(r"(?:greenhouse\.io|lever\.co|ashbyhq\.com)/(?:embed/job_board\?for=)?([\w.-]+)", company.get("careers_url") or "")
    if m:  # the careers link already points at the company's board
        names.append(m.group(1))
    names.append(company.get("slug") or "")
    names.append(re.sub(r"[^a-z0-9]", "", (company.get("name") or "").lower()))
    host = urlparse(company.get("website") or "").netloc.lower().removeprefix("www.")
    if host:
        names.append(host.split(".")[0])
        names.append(host.replace(".", ""))
    names.append(re.sub(r"[^a-z0-9-]", "", (company.get("name") or "").lower().replace(" ", "-")))
    # generic names belong to whoever claimed them first (e.g. board "us" or "solutions")
    return [n for n in dict.fromkeys(names) if len(n) >= 3 and n.lower() not in GENERIC_NAMES]


GENERIC_NAMES = {"us", "corp", "inc", "co", "team", "hq", "app", "apps", "jobs", "careers", "career", "work",
                 "solutions", "agency", "group", "global", "labs", "tech", "digital", "studio", "company",
                 "stage", "keen", "home", "get", "try", "join", "hello", "the"}


def _same_company(name: str, board_name: str) -> bool:
    a = re.sub(r"[^a-z0-9]", "", name.lower())
    b = re.sub(r"[^a-z0-9]", "", board_name.lower())
    return bool(a and b) and (a in b or b in a or a[:5] == b[:5])


def remoteintech_companies(client: httpx.Client) -> list[dict]:
    """Companies from https://remoteintech.company (one Markdown file per company)."""
    repo = "remoteintech/remote-jobs"
    tree = client.get(f"https://api.github.com/repos/{repo}/git/trees/main?recursive=1").json()
    paths = [x["path"] for x in tree.get("tree", [])
             if x["path"].startswith("src/companies/") and x["path"].endswith(".md")]

    def load(path: str) -> dict | None:
        try:
            text = client.get(f"https://raw.githubusercontent.com/{repo}/main/{path}").text
        except httpx.HTTPError:
            return None
        front = text.split("---")[1] if text.startswith("---") else ""
        field = lambda k: (re.search(rf"^{k}:\s*\"?([^\"\n]+)\"?\s*$", front, re.M) or [None, None])[1]
        name = field("title")
        return {"id": path, "name": name.strip(), "slug": field("slug"), "website": field("website"),
                "careers_url": field("careers_url")} if name else None

    with ThreadPoolExecutor(max_workers=16) as pool:
        return [c for c in pool.map(load, paths) if c]


def names_file_companies(path: Path) -> list[dict]:
    """'Company | website' lines from a text file you maintain."""
    out = []
    for line in path.read_text().splitlines():
        if "|" in line and not line.startswith("#"):
            name, site = (p.strip() for p in line.split("|", 1))
            slug = re.sub(r"[^a-z0-9]", "", name.lower())
            out.append({"id": name, "name": name, "slug": slug, "website": f"https://{site}"})
    return out


def find_board(client: httpx.Client, company: dict, services: set[str]) -> tuple[str, str, int] | None:
    for token in board_names(company):
        for service, board_url, feed_url, count in BOARDS:
            if service not in services:
                continue
            try:
                resp = client.get(feed_url.format(t=token))
            except httpx.HTTPError:
                continue
            if resp.status_code == 429:          # being rate limited: back off once and retry
                time.sleep(5)
                resp = client.get(feed_url.format(t=token))
            if resp.status_code != 200:
                continue
            try:
                n = count(resp.json())
            except ValueError:
                continue
            if n > 0:
                if service == "greenhouse":   # Greenhouse names the company: make sure it's the same one
                    try:
                        board = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}").json().get("name", "")
                    except (httpx.HTTPError, ValueError):
                        board = ""
                    if not _same_company(company["name"], board):
                        continue
                return company["name"], board_url.format(t=token), n
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--more", action="store_true",
                        help="search active companies NOT marked as hiring (run after the default search)")
    parser.add_argument("--source", choices=["yc", "remoteintech", "names"], default="yc",
                        help="yc: Y Combinator companies; remoteintech: remoteintech.company directory; "
                             "names: seed/names.txt")
    parser.add_argument("--boards", default="ashby,greenhouse,lever,workable,smartrecruiters",
                        help="comma-separated job-board services to try")
    parser.add_argument("--min-team", type=int, default=0, help="skip companies smaller than this")
    parser.add_argument("--out", default=str(ROOT / "seed" / "discovered.txt"))
    args = parser.parse_args()

    with httpx.Client(timeout=60, follow_redirects=True) as c:
        if args.source == "remoteintech":
            companies = remoteintech_companies(c)
        elif args.source == "names":
            companies = names_file_companies(ROOT / "seed" / "names.txt")
        else:
            companies = c.get(DATASET.format(which="hiring")).json()
        if args.more and args.source == "yc":
            hiring = {x["id"] for x in companies}
            companies = [x for x in c.get(DATASET.format(which="all")).json()
                         if x.get("status") == "Active" and x["id"] not in hiring]
    companies = [x for x in companies if (x.get("team_size") or 0) >= args.min_team]

    # skip companies already known by name
    known = set()
    for line in (ROOT / "seed" / "candidates.txt").read_text().splitlines():
        if "|" in line and not line.startswith("#"):
            known.add(line.split("|")[0].strip().lower())
    for found_file in (ROOT / "seed").glob("discovered*.txt"):
        if found_file.resolve() != Path(args.out).resolve():
            known |= {l.split("|")[0].strip().lower() for l in found_file.read_text().splitlines()
                      if "|" in l and not l.startswith("#")}
    try:
        known |= {s["name"].lower() for s in httpx.get(f"{API}/sites", timeout=30).json()}
    except httpx.HTTPError:
        pass
    todo = [x for x in companies if x["name"].lower() not in known]
    print(f"{len(companies)} companies in the dataset, {len(todo)} not yet watched. Looking for their job boards…", flush=True)

    found, started = [], time.time()
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "JobWatcher/0.1"}) as client, \
            ThreadPoolExecutor(max_workers=16) as pool:
        for i, hit in enumerate(pool.map(lambda x: find_board(client, x, set(args.boards.split(","))), todo), 1):
            if hit:
                found.append(hit)
            if i % 200 == 0:
                print(f"  {i}/{len(todo)} checked, {len(found)} boards found ({time.time() - started:.0f}s)", flush=True)

    found.sort(key=lambda h: h[0].lower())
    out = Path(args.out)
    with out.open("w") as f:
        f.write(f"# Found {time.strftime('%Y-%m-%d %H:%M')} from the Y Combinator company dataset; "
                f"{len(found)} of {len(todo)} companies have a job board with jobs.\n")
        for name, url, n in found:
            f.write(f"{name} | {url}\n")
    print(f"Found {len(found)} job boards → {out}")


if __name__ == "__main__":
    main()
