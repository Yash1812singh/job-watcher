"""Deploy Job Watcher to a free Hugging Face Space (Docker), no credit card needed.

    cd backend && .venv/bin/hf auth login          # once; paste a token with "Write" access
    .venv/bin/python ../deploy/hf_deploy.py        # first deploy, and again after every change

First run creates the Space and a private dataset for database backups, asks for a website
password, and copies your local database into the backup so the Space starts with your companies.
"""
from __future__ import annotations

import getpass
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, get_token

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = Path(os.environ.get(
    "JOB_WATCHER_DB", Path.home() / "Library/Application Support/JobWatcher/jobwatcher.db"))
SPACE_README = """---
title: Job Watcher
emoji: 👀
colorFrom: blue
colorTo: green
sdk: docker
app_port: 8090
pinned: false
---

Watches company careers pages and shows jobs the moment they're posted.
Source: https://github.com/Yash1812singh/job-watcher
"""


def main() -> None:
    token = get_token()
    if not token:
        sys.exit("Not logged in. Run: .venv/bin/hf auth login")
    api = HfApi(token=token)
    user = api.whoami()["name"]
    space, data = f"{user}/job-watcher", f"{user}/job-watcher-data"

    first_time = not api.repo_exists(space, repo_type="space")
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    api.create_repo(data, repo_type="dataset", private=True, exist_ok=True)

    if first_time:
        password = getpass.getpass("Choose a password for the website: ")
        api.add_space_secret(space, "JOB_WATCHER_PASSWORD", password)
        api.add_space_secret(space, "HF_TOKEN", token)
        api.add_space_variable(space, "JOB_WATCHER_BACKUP_REPO", data)
        if LOCAL_DB.exists() and not api.file_exists(data, "jobwatcher.db.gz", repo_type="dataset"):
            print(f"Copying {LOCAL_DB} into the backup…")
            with tempfile.TemporaryDirectory() as tmp:
                snapshot, packed = Path(tmp, "s.db"), Path(tmp, "jobwatcher.db.gz")
                with sqlite3.connect(LOCAL_DB) as live, sqlite3.connect(snapshot) as copy:
                    live.backup(copy)
                with open(snapshot, "rb") as src, gzip.open(packed, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                api.upload_file(path_or_fileobj=str(packed), path_in_repo="jobwatcher.db.gz",
                                repo_id=data, repo_type="dataset")

    print("Uploading code…")
    api.upload_folder(
        folder_path=str(ROOT), repo_id=space, repo_type="space", commit_message="deploy",
        allow_patterns=["Dockerfile", ".dockerignore", "backend/requirements.txt", "backend/app/*.py",
                        "frontend/package.json", "frontend/package-lock.json", "frontend/index.html",
                        "frontend/tsconfig.json", "frontend/vite.config.ts", "frontend/src/*"],
        delete_patterns=["backend/app/*.py", "frontend/src/*"],
    )
    api.upload_file(path_or_fileobj=SPACE_README.encode(), path_in_repo="README.md",
                    repo_id=space, repo_type="space", commit_message="readme")

    host = space.replace("/", "-").lower()
    print(f"\nBuilding (takes ~5 min): https://huggingface.co/spaces/{space}")
    print(f"Website when ready:      https://{host}.hf.space")


if __name__ == "__main__":
    main()
