#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python3"

want_bear_save() {
    case "$2" in
        --save-to-bear|bear)
            return 0
            ;;
    esac

    case "${ALFRED_SAVE_TO_BEAR:-${SAVE_TO_BEAR:-0}}" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
    esac

    return 1
}

run_and_notify() {
    if result=$("$@" 2>&1); then
        notification=$(echo "$result" | tail -1)
        osascript - "$notification" <<'APPLESCRIPT'
on run argv
    display notification (item 1 of argv) with title "Sent to Kindle"
end run
APPLESCRIPT
        return 0
    fi

    osascript - "$result" <<'APPLESCRIPT'
on run argv
    display alert "Push to Kindle Failed" message (item 1 of argv) as critical
end run
APPLESCRIPT
    return 1
}

if [ -n "$1" ] && [ -f "$1" ]; then
    run_and_notify "$PYTHON" "$PROJECT_DIR/send_file_to_kindle.py" "$1"
    exit $?
fi

extra_args=()
if want_bear_save "$1" "$2"; then
    extra_args+=("--save-to-bear")
fi

as_cmd='tell application "Brave Browser" to get URL of active tab of front window'
url=$(osascript -e "$as_cmd" 2>/dev/null)
if [[ -z "$url" || "$url" == "missing value" ]]; then
    url=$(pbpaste)
fi

run_and_notify "$PYTHON" "$PROJECT_DIR/send_to_kindle.py" "${extra_args[@]}" "$url"
