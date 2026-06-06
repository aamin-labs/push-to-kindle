#!/bin/bash
set -euo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

PROJECT_DIR="$HOME/dev/projects/push-to-kindle"
LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/iphone-push-to-kindle.log"
mkdir -p "$LOG_DIR"

URL="${1:-}"
if [[ -z "$URL" && ! -t 0 ]]; then
  URL="$(cat | tr -d '\r' | head -n 1)"
fi

{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---"
  echo "input: ${URL:-<empty>}"
} >> "$LOG_FILE"

if [[ -z "$URL" ]]; then
  echo "Usage: iphone_push_to_kindle.sh <url>" | tee -a "$LOG_FILE" >&2
  exit 64
fi

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python3" send_to_kindle.py "$URL" 2>&1 | tee -a "$LOG_FILE"
