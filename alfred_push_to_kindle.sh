#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python3"

notify_success() {
    osascript - "$1" <<'APPLESCRIPT'
on run argv
    display notification (item 1 of argv) with title "Sent to Kindle"
end run
APPLESCRIPT
}

notify_failure() {
    osascript - "$1" <<'APPLESCRIPT'
on run argv
    display alert "Push to Kindle Failed" message (item 1 of argv) as critical
end run
APPLESCRIPT
}

run_and_notify() {
    result=$("$@" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        notification=$(echo "$result" | tail -1)
        notify_success "$notification"
    else
        notify_failure "$result"
    fi

    return $exit_code
}

if [ -n "$1" ] && [ -f "$1" ]; then
    run_and_notify "$PYTHON" "$PROJECT_DIR/send_file_to_kindle.py" "$1"
    exit $?
fi

as_cmd='tell application "Brave Browser" to get URL of active tab of front window'
url=$(osascript -e "$as_cmd" 2>/dev/null)
if [[ -z "$url" || "$url" == "missing value" ]]; then
    url=$(pbpaste)
fi

run_and_notify "$PYTHON" "$PROJECT_DIR/send_to_kindle.py" "$url"
