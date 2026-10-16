# Job Watcher

Watches company careers pages and shows jobs the moment they're posted.

Add a company's own careers URL (e.g. `https://razorpay.com/jobs`). It's checked every minute by
default, and the page shows only jobs **posted in the last 30 minutes** (change it with the
"Posted in the last" selector). Older jobs drop off the list automatically.

How "posted" is decided:

- If the company's job board gives an exact posting time (Greenhouse, Lever, Ashby,
  SmartRecruiters), that time is used.
- Otherwise it's the moment Job Watcher first saw the job on the page — so with a 1-minute check,
  within about a minute of it going live.
- Jobs already listed when you add a company are treated as old (unless the board's own
  posting time says they're recent), so adding a company doesn't flood the list.

Turn on **alerts** in the top bar to get a desktop notification for each new job while the page is open.

## Run it always-on (recommended)

```bash
./run.sh                       # once, to set up the Python venv and npm packages (then Ctrl+C)
./scripts/install-service.sh   # runs in the background from now on, also after restarts
```

Open **http://localhost:8090**. The service starts at login and restarts itself if it stops.
macOS doesn't let background services read Desktop, so the installer copies the app to
`~/Library/Application Support/JobWatcher` (code, database, logs) and runs it from there.
**After changing the code, run `./scripts/install-service.sh` again** to update the running copy.
Remove it with `./scripts/uninstall-service.sh` (your companies and jobs are kept).

Checks only happen while the Mac is awake. To keep checking with the lid open but the screen off,
use System Settings → Battery/Energy → "Prevent automatic sleeping when the display is off".

## Host it online for free (Render, no card needed)

Render's free plan runs the Docker image; `render.yaml` describes the service. Its disk is wiped
on every restart, so the database is backed up to a private Hugging Face dataset (free) every
15 minutes and restored from it at startup. Free services sleep after 15 minutes without visitors;
the app visits its own address every 10 minutes so the checks keep running.

1. Hugging Face: make an account and a **Write** token (https://huggingface.co/settings/tokens).
   Optionally copy your local companies and jobs into the backup:
   `cd backend && .venv/bin/hf auth login && .venv/bin/python ../deploy/seed_backup.py`
2. Render: sign in with GitHub at https://dashboard.render.com, then open
   https://render.com/deploy?repo=https://github.com/Yash1812singh/job-watcher and paste the token
   as `HF_TOKEN`. Render generates the website password: see Environment → `JOB_WATCHER_PASSWORD`.
3. Every push to `main` redeploys automatically.

The free plan has 512 MB and a small CPU share, so it checks 6 companies at a time and opens
one Chrome at a time (without images or fonts) for the few careers pages that need it.

## Host it on your own server (Oracle Cloud Always Free)

Job Watcher has to run all the time and keep its database, so it needs a small always-on server
rather than a "sleeps when idle" free tier. Oracle Cloud's Always Free tier gives one for free indefinitely.

1. Sign up at https://signup.cloud.oracle.com (a card is asked for to verify identity; Always Free
   resources are never charged).
2. Compute → Instances → Create instance: image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex**
   (Ampere, 2 OCPU / 12 GB is well inside the free limit). Download the SSH key it offers.
3. On the instance's subnet → Security List → add an ingress rule: source `0.0.0.0/0`, TCP port `80`.
4. SSH in (`ssh -i key ubuntu@<public ip>`) and run:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/<you>/job-watcher/main/deploy/server-setup.sh \
     | bash -s -- https://github.com/<you>/job-watcher.git
   ```

   It installs Docker, asks for a website password and starts the app at `http://<public ip>`.
   Your browser asks for the password once (any username).

After pushing changes to GitHub, run `~/job-watcher/deploy/update.sh` on the server.
To move your current companies and jobs over, stop the app on the server (`sudo docker compose stop`),
copy `~/Library/Application Support/JobWatcher/jobwatcher.db` to `~/job-watcher/data/jobwatcher.db`
with `scp`, then `sudo docker compose start`.

## Run for development

```bash
./run.sh
```

Then open http://localhost:5173 (hot-reloading frontend). Stop the service first
(`./scripts/uninstall-service.sh`) since both use port 8090. Both use the same database in
`~/Library/Application Support/JobWatcher/jobwatcher.db`.

## Adding lots of companies

`backend/seed/candidates.txt` lists companies as `Name | careers or job-board URL`.

```bash
cd backend && .venv/bin/python scripts/seed_companies.py
```

checks every line against the live site (also trying Greenhouse/Lever/Ashby under the same name
when the first guess fails), adds the ones that return jobs to the running app, skips ones already
watched, and writes what worked and what didn't to `backend/seed/verified.txt`. Add your own lines
and run it again any time.

Needs Python 3.10+ (3.12 used), Node 18+ (20 recommended), and Google Chrome (used headlessly for
careers pages that build their job list with JavaScript).

## How jobs are found

For each careers page, in order:

1. **The company's hosted job board.** Many careers pages load their list from a hiring system
   (Greenhouse, Lever, Ashby, Workable, SmartRecruiters). When the page embeds or links to one,
   that company's board is read from its public JSON feed — complete, with locations and teams.
2. **Job data in the page** (schema.org `JobPosting`).
3. **Job-like links** on the page, e.g. `/careers/details/27694` or `acme.keka.com/careers/jobdetails/123`.

If the plain page has nothing, it's opened in headless Chrome and steps 1–3 run again.
Each company shows which method worked, or a note explaining why nothing was found.

## Layout

```
backend/   FastAPI + SQLite (jobwatcher.db)
  app/scraper.py   finds jobs on one careers page
  app/monitor.py   schedules checks, records new / closed jobs
  app/main.py      REST API under /api
  tests/           pytest
frontend/  React + Vite (proxies /api to the backend)
```

## Finding more companies automatically

`scripts/discover_companies.py` looks up job boards for whole lists of companies and writes the
ones it finds to a `seed/discovered*.txt` file, which `seed_companies.py` then checks and adds:

```bash
cd backend
.venv/bin/python scripts/discover_companies.py                                   # Y Combinator companies that are hiring
.venv/bin/python scripts/discover_companies.py --more --out seed/discovered-more.txt          # other active YC companies
.venv/bin/python scripts/discover_companies.py --source remoteintech --out seed/discovered-remote.txt   # remoteintech.company
.venv/bin/python scripts/discover_companies.py --source names --out seed/discovered-names.txt # your list in seed/names.txt
.venv/bin/python scripts/seed_companies.py --file seed/discovered.txt --interval 5
```

For each company it tries likely board names (short name, company name, website domain) on Ashby,
Greenhouse, Lever, Workable and SmartRecruiters. Generic names (`us`, `corp`, `solutions`…) are
skipped and Greenhouse boards must carry the same company name, so a board isn't mixed up with a
different company that happens to share a name. Companies already watched are skipped.

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

## Settings

| Env var | Default | |
|---|---|---|
| `JOB_WATCHER_DB` | `~/Library/Application Support/JobWatcher/jobwatcher.db` | SQLite file |
| `JOB_WATCHER_RENDER` | `1` | Set `0` to never open pages in Chrome |
| `JOB_WATCHER_MAX_BROWSERS` | `2` | How many Chrome-based checks may run at once |
| `JOB_WATCHER_FRONTEND` | `frontend/dist` | Built website served at `/` |
| `JOB_WATCHER_BROWSER_CHANNEL` | `chrome` | Empty = Playwright's bundled Chromium (used in Docker) |
| `JOB_WATCHER_PASSWORD` | unset | When set, the site and API ask for this password |
| `JOB_WATCHER_BACKUP_REPO` | unset | Hugging Face dataset to back the database up to (needs `HF_TOKEN`) |
| `JOB_WATCHER_BACKUP_MINUTES` | `15` | How often to back up |
| `JOB_WATCHER_MAX_PARALLEL` | `24` | Companies checked at the same time |

## Known limits

- Checks only run while the backend is running. Jobs posted while it's stopped are still picked
  up on the next check, but are dated from when they were found unless the board gives a time.
- Paged or "load more" lists (e.g. a search page showing 10 at a time) only give the first page;
  point it at the company's full job board when there is one.
- Link matching is a best guess; a careers page with unusual URLs may show a few non-job links or miss some.
