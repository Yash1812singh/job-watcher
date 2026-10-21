#!/usr/bin/env bash
# Builds the Chrome Web Store upload: dist/fill-with-ai-<version>.zip (see extension/STORE.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$ROOT/extension/manifest.json")
OUT="$ROOT/dist/fill-with-ai-$VERSION.zip"
mkdir -p "$ROOT/dist"
rm -f "$OUT"
(cd "$ROOT/extension" && zip -q -X "$OUT" manifest.json background.js content.js connector.js options.html options.js icon128.png)
echo "$OUT ($(du -h "$OUT" | cut -f1))"
