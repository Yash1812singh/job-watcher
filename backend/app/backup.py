"""Keep the database safe on hosts whose disk is wiped on restart (e.g. Hugging Face Spaces).

With JOB_WATCHER_BACKUP_REPO (a private Hugging Face dataset, e.g. "you/job-watcher-data") and
HF_TOKEN set, the database is restored from that dataset at startup and uploaded to it every
JOB_WATCHER_BACKUP_MINUTES. Without them, nothing here runs.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import os
import shutil
import sqlite3
import tempfile

from . import db

log = logging.getLogger(__name__)

REPO = os.environ.get("JOB_WATCHER_BACKUP_REPO", "")
INTERVAL_MINUTES = int(os.environ.get("JOB_WATCHER_BACKUP_MINUTES", "15"))
FILENAME = "jobwatcher.db.gz"


def enabled() -> bool:
    return bool(REPO and os.environ.get("HF_TOKEN"))


def restore() -> None:
    """Download the last backup if there's no database yet (fresh container)."""
    if not enabled() or os.path.exists(db.DB_PATH):
        return
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    try:
        path = hf_hub_download(REPO, FILENAME, repo_type="dataset")
    except (EntryNotFoundError, RepositoryNotFoundError):
        log.info("no backup in %s yet; starting with an empty database", REPO)
        return
    os.makedirs(os.path.dirname(os.path.abspath(db.DB_PATH)), exist_ok=True)
    with gzip.open(path, "rb") as src, open(db.DB_PATH, "wb") as dst:
        shutil.copyfileobj(src, dst)
    log.info("restored database from %s", REPO)


def backup() -> None:
    """Upload a consistent snapshot, keeping only the latest one so the dataset doesn't grow."""
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(REPO, repo_type="dataset", private=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = os.path.join(tmp, "snapshot.db")
        with sqlite3.connect(db.DB_PATH) as live, sqlite3.connect(snapshot) as copy:
            live.backup(copy)
        packed = os.path.join(tmp, FILENAME)
        with open(snapshot, "rb") as src, gzip.open(packed, "wb") as dst:
            shutil.copyfileobj(src, dst)
        api.upload_file(path_or_fileobj=packed, path_in_repo=FILENAME, repo_id=REPO,
                        repo_type="dataset", commit_message="backup")
    api.super_squash_history(REPO, repo_type="dataset")


async def run_forever(stop: asyncio.Event) -> None:
    if not enabled():
        return
    log.info("backing up to %s every %s min", REPO, INTERVAL_MINUTES)
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_MINUTES * 60)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(backup)
        except Exception:
            log.exception("backup failed")
        if stop.is_set():
            return
