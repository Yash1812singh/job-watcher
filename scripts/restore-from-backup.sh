#!/usr/bin/env bash
# Move the hosted Job Watcher's data (companies, jobs, Fill with AI accounts) onto this Mac:
# downloads the latest backup from the private Hugging Face dataset and puts it in place of the
# local database (which is kept as old-<date>-jobwatcher.db). Then run scripts/install-service.sh.
#
#   JOB_WATCHER_BACKUP_REPO=you/job-watcher-data bash scripts/restore-from-backup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Library/Application Support/JobWatcher"
REPO="${JOB_WATCHER_BACKUP_REPO:?set JOB_WATCHER_BACKUP_REPO to your backup dataset, e.g. you/job-watcher-data}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading the latest backup from $REPO…"
"$ROOT/backend/.venv/bin/python" - "$REPO" "$TMP" <<'PY'
import sys
from huggingface_hub import hf_hub_download
hf_hub_download(sys.argv[1], "jobwatcher.db.gz", repo_type="dataset", local_dir=sys.argv[2])
PY
gunzip "$TMP/jobwatcher.db.gz"
[[ "$(sqlite3 "$TMP/jobwatcher.db" 'pragma integrity_check')" == "ok" ]] || { echo "The backup is damaged; nothing changed."; exit 1; }
echo "Backup has $(sqlite3 "$TMP/jobwatcher.db" 'select count(*) from sites') companies and $(sqlite3 "$TMP/jobwatcher.db" 'select count(*) from jobs') jobs."

launchctl bootout "gui/$(id -u)/com.jobwatcher.server" 2>/dev/null || true
for _ in $(seq 1 30); do launchctl print "gui/$(id -u)/com.jobwatcher.server" >/dev/null 2>&1 || break; sleep 1; done
mkdir -p "$APP"
STAMP="$(date +%Y-%m-%d)"
for f in jobwatcher.db jobwatcher.db-wal jobwatcher.db-shm; do
  [[ -f "$APP/$f" ]] && mv "$APP/$f" "$APP/old-$STAMP-$f"
done
cp "$TMP/jobwatcher.db" "$APP/jobwatcher.db"
echo "Restored. Your previous local database is kept as $APP/old-$STAMP-jobwatcher.db"
