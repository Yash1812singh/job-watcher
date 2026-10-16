"""Check every company in seed/candidates.txt against its live careers page and add the ones
that return jobs to the running Job Watcher.

    cd backend && .venv/bin/python scripts/seed_companies.py            # check + add
    cd backend && .venv/bin/python scripts/seed_companies.py --dry-run  # check only

Companies already being watched (same URL) are skipped. Results are written to
<file>-verified.txt (e.g. seed/candidates-verified.txt) so you can see what worked and what didn't.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scraper import ScanError, detect_ats, scan  # noqa: E402

WORKDAY_SERVERS = ["wd1", "wd3", "wd5", "wd12", "wd103", "wd108"]
ALT_BOARDS = [
    "https://boards.greenhouse.io/{token}",
    "https://jobs.lever.co/{token}",
    "https://jobs.ashbyhq.com/{token}",
]

API = "http://127.0.0.1:8090/api"
# Hosted job boards give exact posting times, so a 2-minute check still shows the true
# "posted N min ago". Pages read through the browser are heavier, so they're checked less often.
INTERVAL_BOARD, INTERVAL_PAGE, INTERVAL_BROWSER = 2, 2, 5


def load_candidates(path: Path) -> list[tuple[str, str]]:
    rows, seen_urls = [], set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        name, url = (p.strip() for p in line.split("|", 1))
        if url not in seen_urls:
            seen_urls.add(url)
            rows.append((name, url))
    return rows


def check(candidate: tuple[str, str]) -> dict:
    name, url = candidate
    started = time.time()
    try:
        result = scan(url)
        ok = bool(result.jobs) and not result.weak
        return {"name": name, "url": url, "ok": ok, "jobs": len(result.jobs), "method": result.method,
                "seconds": round(time.time() - started, 1),
                "why": None if ok else (result.note or "no jobs")}
    except (ScanError, Exception) as e:  # one broken site shouldn't stop the run
        return {"name": name, "url": url, "ok": False, "jobs": 0, "method": None,
                "seconds": round(time.time() - started, 1), "why": str(e)[:160]}


def interval_for(method: str) -> int:
    if method.endswith("(rendered)"):
        return INTERVAL_BROWSER
    return INTERVAL_BOARD if method in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters",
                                        "workday", "amazon", "eightfold") else INTERVAL_PAGE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="check the candidates but don't add them")
    parser.add_argument("--file", default=str(ROOT / "seed" / "candidates.txt"))
    parser.add_argument("--interval", type=int, help="check every N minutes for all added companies")
    args = parser.parse_args()

    candidates = load_candidates(Path(args.file))
    print(f"Checking {len(candidates)} candidate URLs…", flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(check, candidates))

    # A board name that isn't on one hiring system is often on another: retry failures there.
    names_ok = {r["name"] for r in results if r["ok"]}
    retry = []
    for r in results:
        ats = detect_ats(r["url"])
        if r["ok"] or r["name"] in names_ok or not ats:
            continue
        if ats[0] == "workday":   # same tenant/site, different Workday server number
            tenant, wd, site = ats[1].split("/")
            retry += [(r["name"], f"https://{tenant}.{n}.myworkdayjobs.com/{site}")
                      for n in WORKDAY_SERVERS if n != wd]
            continue
        for alt in ALT_BOARDS:
            alt_url = alt.format(token=ats[1])
            if detect_ats(alt_url)[0] != ats[0]:
                retry.append((r["name"], alt_url))
    if retry:
        print(f"Trying {len(retry)} alternative job-board URLs for companies that failed…", flush=True)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results += list(pool.map(check, retry))

    # one entry per company: if a company has two candidate URLs, keep the one with more jobs
    best: dict[str, dict] = {}
    for r in results:
        if r["ok"] and (r["name"] not in best or r["jobs"] > best[r["name"]]["jobs"]):
            best[r["name"]] = r
    working = sorted(best.values(), key=lambda r: r["name"].lower())
    failed = [r for r in results if not r["ok"] and r["name"] not in best]

    source = Path(args.file).resolve()
    report = source.with_name(source.stem + "-verified.txt")
    with report.open("w") as f:
        f.write(f"# Checked {time.strftime('%Y-%m-%d %H:%M')}: {len(working)} working, {len(failed)} not working\n\n")
        for r in working:
            f.write(f"OK   {r['name']} | {r['url']} | {r['jobs']} jobs via {r['method']}\n")
        f.write("\n")
        for r in sorted(failed, key=lambda r: r["name"].lower()):
            f.write(f"FAIL {r['name']} | {r['url']} | {r['why']}\n")
    print(f"{len(working)} companies work, {len(failed)} don't (details in {report.relative_to(ROOT)})")

    if args.dry_run:
        return

    with httpx.Client(timeout=30) as api:
        watched = {s["url"] for s in api.get(f"{API}/sites").json()}
        added = 0
        for r in working:
            if r["url"] in watched:
                continue
            res = api.post(f"{API}/sites", json={
                "url": r["url"], "name": r["name"], "interval_minutes": args.interval or interval_for(r["method"]),
                "scan_now": False,   # the scheduler picks them up, a few at a time
            })
            if res.status_code == 201:
                added += 1
            elif res.status_code != 409:
                print(f"  couldn't add {r['name']}: {res.status_code} {res.text[:120]}")
    print(f"Added {added} new companies; {len(working) - added} were already being watched.")


if __name__ == "__main__":
    main()
