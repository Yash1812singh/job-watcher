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
BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{t}",
    "lever": "https://jobs.lever.co/{t}",
    "ashby": "https://jobs.ashbyhq.com/{t}",
}


def get(client: httpx.Client, endpoint: str, **params) -> httpx.Response | None:
    for attempt in range(4):           # the index is slow and sometimes times out; be patient
        try:
            r = client.get(endpoint, params=params)
            if r.status_code == 200:
                return r
            if r.status_code in (404,):
                return None
        except httpx.HTTPError:
            pass
        time.sleep(5 * (attempt + 1))
    return None


def board_from(url: str) -> str | None:
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
    args = parser.parse_args()

    boards: set[str] = set()
    with httpx.Client(timeout=180, headers={"User-Agent": "JobWatcher/0.1 (personal job tracker)"}) as client:
        crawls = client.get("https://index.commoncrawl.org/collinfo.json").json()[: args.crawls]
        for crawl in crawls:
            api = crawl["cdx-api"]
            for pattern in PATTERNS:
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
                print(f"{crawl['id']} {pattern:<28} {pages} pages, +{len(boards) - before} boards "
                      f"(total {len(boards)})", flush=True)

    out = Path(args.out)
    out.write_text("".join(f"{b}\n" for b in sorted(boards)))
    print(f"{len(boards)} distinct job boards → {out}")


if __name__ == "__main__":
    main()
