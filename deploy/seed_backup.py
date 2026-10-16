"""Copy your local database into the Hugging Face backup dataset, so a fresh deploy starts
with your companies and jobs instead of an empty list.

    cd backend && .venv/bin/hf auth login         # once
    .venv/bin/python ../deploy/seed_backup.py [user/dataset]

Stop the hosted app first if it's running, or its next backup will overwrite this one.
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

LOCAL_DB = Path(os.environ.get(
    "JOB_WATCHER_DB", Path.home() / "Library/Application Support/JobWatcher/jobwatcher.db"))


def main() -> None:
    api = HfApi()
    repo = sys.argv[1] if len(sys.argv) > 1 else f"{api.whoami()['name']}/job-watcher-data"
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot, packed = Path(tmp, "s.db"), Path(tmp, "jobwatcher.db.gz")
        with sqlite3.connect(LOCAL_DB) as live, sqlite3.connect(snapshot) as copy:
            live.backup(copy)
        with open(snapshot, "rb") as src, gzip.open(packed, "wb") as dst:
            shutil.copyfileobj(src, dst)
        api.upload_file(path_or_fileobj=str(packed), path_in_repo="jobwatcher.db.gz",
                        repo_id=repo, repo_type="dataset", commit_message="seed from local database")
        size_mb = packed.stat().st_size / 1e6
    api.super_squash_history(repo, repo_type="dataset")
    print(f"Uploaded {LOCAL_DB} ({size_mb:.1f} MB compressed) to {repo}")


if __name__ == "__main__":
    main()
