# push-to-kindle

Send any web article to your Kindle in one command. Extracts the core content, strips clutter, and delivers it as a clean HTML document via your email.

## Installation

```bash
git clone https://github.com/your-username/push-to-kindle.git
cd push-to-kindle
./setup.sh
```

## Configuration

Edit `.env` (created by setup):

```
KINDLE_EMAIL=yourname@kindle.com
```

See `.env.example` for SMTP settings.

**Prerequisites:**

1. Add your sending email to Amazon's approved senders list:
   Amazon account → Manage Your Content and Devices → Preferences → Personal Document Settings → Approved Personal Document E-mail List

2. Set `SMTP_SERVER`, `SMTP_USER`, and `SMTP_PASSWORD` in `.env`. Use an app-specific password, not your main account password.

## Usage

```bash
source .venv/bin/activate

# Send an article to Kindle
python3 send_to_kindle.py "https://example.com/article"

# Skip image downloading for faster sends
python3 send_to_kindle.py --no-images "https://example.com/article"

# Preview extraction locally without sending
python3 send_to_kindle.py --dry-run "https://example.com/article"

# Send a local file directly to Kindle
python3 send_file_to_kindle.py "/path/to/book.pdf"
```

The article is extracted, wrapped in a clean HTML document, and delivered to your Kindle. It appears on your device within a minute or two.

On macOS, a Bear note is also created automatically — tagged `#0a/reading`, with the article title, URL, date, and full article body in Markdown. The note ID is saved to `~/logs/kindle-bear-map.json` for highlight sync later.

## Syncing highlights

When your Kindle is plugged in via USB, run:

```bash
python3 sync_highlights.py
```

This parses `My Clippings.txt`, matches each document to its Bear note via `~/logs/kindle-bear-map.json`, and inlines your highlights as `==highlighted text==` directly in the article body. Passages that can't be located in the note are appended under `## Unmatched Highlights`. A seen-log (`~/logs/kindle-seen.json`) prevents re-syncing highlights across runs.

```bash
# Preview without touching Bear
python3 sync_highlights.py --dry-run

# Use a custom clippings path
python3 sync_highlights.py --clippings /path/to/My\ Clippings.txt
```

**Auto-sync on plug-in (macOS):** A launchd agent watches for the clippings file and fires the sync automatically:

```bash
launchctl load ~/Library/LaunchAgents/com.aamin.kindle-sync.plist
```

Check `~/logs/kindle-sync.log` to see sync output.

## Alfred workflow (macOS)

Trigger a send from any article open in Brave without leaving the browser:

1. Create a new Alfred workflow with a **Keyword** input (no argument)
2. Connect it to a **Run Script** action (`/bin/bash`) with:

```bash
PROJECT_DIR="/Users/yourname/dev/projects/push-to-kindle"

as_cmd='tell application "Brave Browser" to get URL of active tab of front window'
URL=$(osascript -e "$as_cmd" 2>/dev/null)
if [[ -z "$URL" || "$URL" == "missing value" ]]; then
  URL=$(pbpaste)
fi

result=$("$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/send_to_kindle.py" "$URL" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    notification=$(echo "$result" | tail -1)
    osascript -e "display notification \"$notification\" with title \"Sent to Kindle\""
else
    osascript -e "display alert \"Push to Kindle Failed\" message \"$result\" as critical"
fi
```

The script grabs the active tab URL from Brave automatically and passes it to the Python script. defuddle.md URLs are handled natively by the script (see below). Falls back to clipboard if Brave isn't frontmost.

### Alfred file action

Send a selected Finder file directly to Kindle:

1. Create a new Alfred workflow with a **File Action** input
2. Connect it to a **Run Script** action (`/bin/bash`) with:

```bash
PROJECT_DIR="/Users/yourname/dev/projects/push-to-kindle"

result=$("$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/send_file_to_kindle.py" "$1" 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    notification=$(echo "$result" | tail -1)
    osascript -e "display notification \"$notification\" with title \"Sent to Kindle\""
else
    osascript -e "display alert \"Push to Kindle Failed\" message \"$result\" as critical"
fi
```

This path attaches the selected file unchanged and sends it with the same Gmail SMTP settings. Kindle file compatibility is handled by Amazon's Personal Documents service.

## How it works

**URL path (normal articles):**
1. Fetches the page with `requests` / `trafilatura`
2. Extracts article content with `trafilatura`
3. Downloads and embeds images as base64 data URIs (skip with `--no-images`)
4. Wraps it in a minimal HTML document with readable typography
5. Sends it via SMTP
6. Creates a Bear note tagged `#0a/reading` with the article Markdown body (macOS, URL path only)

**Selected file path (Finder / Alfred File Action):**
1. Validates the selected path is a readable file
2. Attaches the original file bytes without conversion
3. Sends the attachment via SMTP using the original filename
4. Skips Bear note creation and article metadata sync

**defuddle.md path (hard-to-extract articles, e.g. X/Twitter):**
1. Script detects `https://defuddle.md/*` URLs automatically, and rewrites direct `https://x.com/*` URLs to `https://defuddle.md/x.com/*`
2. Fetches clean markdown directly from the defuddle.md API (`text/markdown` response with YAML frontmatter)
3. Converts to EPUB using pandoc — renders better than HTML for markdown content on Kindle
4. Sends EPUB to Kindle; creates Bear note with the original article URL from the frontmatter `source:` field

> **Requires:** pandoc installed (`brew install pandoc`).

## Requirements

- Python 3.9+
- SMTP credentials for the sender account
- Amazon Kindle with Personal Documents enabled

## License

MIT
