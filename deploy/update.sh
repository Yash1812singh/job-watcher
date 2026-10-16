#!/usr/bin/env bash
# On the server: pull the latest code from GitHub and restart. Companies and jobs in ./data are kept.
set -euo pipefail
cd ~/job-watcher
git pull --ff-only
sudo docker compose up -d --build
