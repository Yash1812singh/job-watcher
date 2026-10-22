"""Keep the job boards that are hiring in India.

Reads board URLs (one per line, e.g. from scripts/crawl_boards.py), opens each board once, and
counts its open jobs located in India (and how many of those are technical). Boards with at least
one India job are written to seed/india-boards.txt as "Name | URL", ready for seed_companies.py:

    cd backend && .venv/bin/python scripts/find_india_boards.py
    cd backend && .venv/bin/python scripts/seed_companies.py --file seed/india-boards.txt --interval 10

Boards already being watched are skipped.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.classify import is_india, is_tech  # noqa: E402
from app.scraper import ScanError, detect_ats, scan  # noqa: E402

# The Job Watcher to read and add companies to: the local one, or the hosted one with its password
#   JOB_WATCHER_API=https://<your site>/api JOB_WATCHER_PASSWORD=... .venv/bin/python scripts/...
API = os.environ.get("JOB_WATCHER_API", "http://127.0.0.1:8090/api").rstrip("/")
AUTH = ("me", os.environ["JOB_WATCHER_PASSWORD"]) if os.environ.get("JOB_WATCHER_PASSWORD") else None


def company_name(client: httpx.Client, url: str) -> str:
    """A readable company name for a board: Greenhouse knows it, the others only have a slug."""
    service, token = detect_ats(url)
    if service == "greenhouse":
        try:
            name = client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}").json().get("name")
            if name:
                return name.strip()
        except (httpx.HTTPError, ValueError):
            pass
    if service == "workday":
        token = token.split("/")[0]
    words = re.sub(r"[-_.]+", " ", token).strip()
    return words.title() if words.islower() else words


def check(url: str) -> dict | None:
    try:
        result = scan(url, render=False)
    except (ScanError, Exception):
        return None
    india = [j for j in result.jobs if is_india(j.location)]
    if not india:
        return None
    return {"url": url, "jobs": len(result.jobs), "india": len(india),
            "india_tech": sum(1 for j in india if is_tech(j.title, j.department))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(ROOT / "seed" / "crawl-boards.txt"))
    parser.add_argument("--out", default=str(ROOT / "seed" / "india-boards.txt"))
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    boards = [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    try:
        watched = {s["url"].lower().rstrip("/") for s in httpx.get(f"{API}/sites", params={"posted_within": 60}, auth=AUTH, timeout=120).json()}
    except httpx.HTTPError:
        watched = set()
    todo = [b for b in boards if b.lower().rstrip("/") not in watched]
    print(f"{len(boards)} boards, {len(todo)} not yet watched. Checking which are hiring in India…", flush=True)

    found, started = [], time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, hit in enumerate(pool.map(check, todo), 1):
            if hit:
                found.append(hit)
            if i % 500 == 0:
                print(f"  {i}/{len(todo)} checked, {len(found)} hiring in India ({time.time() - started:.0f}s)", flush=True)

    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(lambda h: company_name(client, h["url"]), found))
    rows = sorted(zip(names, found), key=lambda x: (-x[1]["india_tech"], x[0].lower()))
    out = Path(args.out)
    with out.open("w") as f:
        f.write(f"# {time.strftime('%Y-%m-%d %H:%M')}: {len(rows)} of {len(todo)} boards have open jobs in India "
                f"({sum(h['india'] for _, h in rows)} India jobs, {sum(h['india_tech'] for _, h in rows)} technical)\n")
        for name, h in rows:
            f.write(f"{name} | {h['url']}\n")
    print(f"{len(rows)} boards hiring in India → {out}")


if __name__ == "__main__":
    main()
