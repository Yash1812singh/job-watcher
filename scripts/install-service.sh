#!/usr/bin/env bash
# Keeps Job Watcher running in the background: starts at login, restarts if it stops.
# The website and API are served together at http://localhost:8090.
#
# macOS doesn't let background services read Desktop/Documents, so this copies the app to
# ~/Library/Application Support/JobWatcher and runs it from there. Run this script again
# after changing the code to update the running copy. Undo with scripts/uninstall-service.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Library/Application Support/JobWatcher"
LABEL="com.jobwatcher.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3)}"

command -v node >/dev/null || { echo "Node.js is needed to build the website."; exit 1; }
mkdir -p "$APP/logs" "$HOME/Library/LaunchAgents"

echo "Building the website…"
(cd "$ROOT/frontend" && npm install --silent && npx vite build >/dev/null)

echo "Copying the app to ${APP}…"
rsync -a --delete --exclude __pycache__ "$ROOT/backend/app/" "$APP/app/"
rsync -a --delete "$ROOT/frontend/dist/" "$APP/web/"
cp "$ROOT/backend/requirements.txt" "$APP/requirements.txt"

if [[ ! -x "$APP/venv/bin/uvicorn" ]] || ! cmp -s "$ROOT/backend/requirements.txt" "$APP/.installed-requirements"; then
  echo "Installing Python packages…"
  [[ -d "$APP/venv" ]] || "$PYTHON" -m venv "$APP/venv"
  "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt" playwright
  cp "$ROOT/backend/requirements.txt" "$APP/.installed-requirements"
fi

# Bring over companies/jobs saved by an older version that kept its database in the project.
OLD_DB="$ROOT/backend/jobwatcher.db"
if [[ -f "$OLD_DB" && ! -f "$APP/jobwatcher.db" ]]; then
  echo "Moving your saved companies and jobs to ${APP}…"
  sqlite3 "$OLD_DB" ".backup '$APP/jobwatcher.db'"
fi

# "Fill with AI" (the browser extension in extension/): copy your AutoApply profile, resume and
# Groq key where the service can read them (it can't read Desktop). Re-run after editing them.
AUTOAPPLY="${AUTOAPPLY:-$HOME/Desktop/Python/autoapply}"
FILL="$APP/autofill"
if [[ -f "$AUTOAPPLY/config/user_profile.yaml" ]]; then
  mkdir -p "$FILL" && chmod 700 "$FILL"
  cp "$AUTOAPPLY/config/user_profile.yaml" "$FILL/profile.yaml"
  [[ -f "$AUTOAPPLY/data/resume.pdf" ]] && cp "$AUTOAPPLY/data/resume.pdf" "$FILL/resume.pdf"
  (umask 077; grep -E '^GROQ_API_KEY=' "$AUTOAPPLY/.env" 2>/dev/null > "$FILL/keys.env" || true)
  echo "Fill with AI: using the profile and resume from $AUTOAPPLY"
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/venv/bin/uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8090</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JOB_WATCHER_DB</key><string>$APP/jobwatcher.db</string>
    <key>JOB_WATCHER_FRONTEND</key><string>$APP/web</string>
    <key>JOB_WATCHER_APPLY_PROFILE</key><string>$FILL/profile.yaml</string>
    <key>JOB_WATCHER_APPLY_RESUME</key><string>$FILL/resume.pdf</string>
    <key>JOB_WATCHER_APPLY_KEYS</key><string>$FILL/keys.env</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$APP/logs/jobwatcher.log</string>
  <key>StandardErrorPath</key><string>$APP/logs/jobwatcher.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# wait for the old copy to exit before starting the new one
for _ in $(seq 1 60); do launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break; sleep 1; done
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Job Watcher is running in the background → http://localhost:8090"
echo "Logs: $APP/logs/jobwatcher.log"
