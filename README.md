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

# Also save the article to Bear (macOS only)
python3 send_to_kindle.py --save-to-bear "https://example.com/article"

# Send a local file directly to Kindle
python3 send_file_to_kindle.py "/path/to/book.pdf"
```

The article is extracted, wrapped in a clean HTML document, and delivered to your Kindle. It appears on your device within a minute or two.

On macOS, pass `--save-to-bear` if you also want a Bear note — tagged `#0a/reading`, with the article title, URL, date, and full article body in Markdown. The note ID is saved to `~/logs/kindle-bear-map.json` for highlight sync later.

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

Trigger a send from either an article open in Brave or a selected Finder file:

1. Create a **Keyword** input and a **File Action** input
2. Connect both to the same **Run Script** action
3. Set the Run Script language to `/bin/bash` and input handling to **with input as argv**
4. Use this script:

```bash
PROJECT_DIR="/Users/yourname/dev/projects/push-to-kindle"
bash "$PROJECT_DIR/alfred_push_to_kindle.sh" "$1" "$2"
```

The wrapper checks Alfred's argv first. If `$1` is a selected file, it sends that file directly; otherwise it grabs the active Brave URL and falls back to the clipboard. Markdown files (`.md` / `.markdown`) are converted to `.html` attachments before sending so headers, lists, links, code blocks, and quotes render more readably on Kindle; other files are sent unchanged.

**Bear toggle:** Alfred can opt into Bear note creation for URL sends by either:
- passing `--save-to-bear` as the second argv value
- or setting `ALFRED_SAVE_TO_BEAR=1` (or `SAVE_TO_BEAR=1`) in the Run Script environment

Good setup: keep your normal action as-is, then make a second Alfred trigger that runs the same script with `$2` set to `--save-to-bear`.

## How it works

**URL path (normal articles):**
1. Fetches the page with `requests` / `trafilatura`
2. Extracts article content with `trafilatura`
3. Downloads and embeds images as base64 data URIs (skip with `--no-images`)
4. Wraps it in a minimal HTML document with readable typography
5. Sends it via SMTP
6. Optionally creates a Bear note tagged `#0a/reading` with the article Markdown body when `--save-to-bear` is passed (macOS, URL path only)

**Selected file path (Finder / Alfred File Action):**
1. Validates the selected path is a readable file
2. Sends Markdown files as `.txt` attachments without changing the source file
3. Sends other attachments via SMTP using the original filename
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
