#!/usr/bin/env bash
# Run once on a fresh Ubuntu server (e.g. Oracle Cloud Always Free):
#   curl -fsSL https://raw.githubusercontent.com/<you>/job-watcher/main/deploy/server-setup.sh | bash -s -- https://github.com/<you>/job-watcher.git
# Installs Docker, clones the repo to ~/job-watcher, asks for a password and starts the app on port 80.
set -euo pipefail
REPO="${1:?usage: server-setup.sh <git repo url>}"

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
# Oracle's Ubuntu images block port 80 in iptables even when the cloud firewall allows it.
if sudo iptables -L INPUT -n 2>/dev/null | grep -q REJECT; then
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo netfilter-persistent save 2>/dev/null || true
fi

[[ -d ~/job-watcher ]] || git clone "$REPO" ~/job-watcher
cd ~/job-watcher
if [[ ! -f .env ]]; then
  read -rsp "Choose a password for the website: " pw </dev/tty; echo
  echo "JOB_WATCHER_PASSWORD=$pw" > .env
fi
mkdir -p data
sudo docker compose up -d --build
echo "Job Watcher is running on http://$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | cut -d' ' -f1)"
