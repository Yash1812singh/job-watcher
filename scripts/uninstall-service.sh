#!/usr/bin/env bash
# Stops the background Job Watcher and removes it from login. Your saved companies and jobs
# (~/Library/Application Support/JobWatcher/jobwatcher.db) are kept; ./run.sh still uses them.
set -euo pipefail
LABEL="com.jobwatcher.server"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Job Watcher background service removed."
