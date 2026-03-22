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

See `.env.example` for all options including `SENDER_EMAIL` and SMTP settings.

**Prerequisites:**

1. Add your sending email to Amazon's approved senders list:
   Amazon account → Manage Your Content and Devices → Preferences → Personal Document Settings → Approved Personal Document E-mail List

2. **macOS:** Mail.app must be configured with the account you want to send from. Set `SENDER_EMAIL` in `.env` if you have multiple accounts — the script prints which address it uses so you know what to whitelist.

3. **Linux / Windows:** Set `SMTP_SERVER`, `SMTP_USER`, and `SMTP_PASSWORD` in `.env`. Use an app-specific password, not your main account password.

## Usage

```bash
source .venv/bin/activate

# Send an article to Kindle
python3 send_to_kindle.py "https://example.com/article"

# Skip image downloading for faster sends
python3 send_to_kindle.py --no-images "https://example.com/article"

# Preview extraction locally without sending
python3 send_to_kindle.py --dry-run "https://example.com/article"
```

The article is extracted, wrapped in a clean HTML document, and delivered to your Kindle. It appears on your device within a minute or two.

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

if [[ "$URL" == https://defuddle.md/* ]]; then
  # Grab the rendered article HTML and title directly from the live Brave tab
  PAGE_HTML=$(osascript -e 'tell application "Brave Browser" to execute front window'"'"'s active tab javascript "var el = document.querySelector('"'"'article,main,[class*=content],.markdown'"'"'); el ? el.innerHTML : document.body.innerHTML"' 2>/dev/null)
  PAGE_TITLE=$(osascript -e 'tell application "Brave Browser" to execute front window'"'"'s active tab javascript "document.title"' 2>/dev/null)
  TMPFILE=$(mktemp /tmp/defuddleXXXXXX)
  printf '%s' "$PAGE_HTML" > "$TMPFILE"
  result=$("$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/send_to_kindle.py" --html-file "$TMPFILE" --title "$PAGE_TITLE" 2>&1)
  EXIT_CODE=$?
  rm -f "$TMPFILE"
else
  result=$("$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/send_to_kindle.py" "$URL" 2>&1)
  EXIT_CODE=$?
fi

if [ $EXIT_CODE -eq 0 ]; then
    notification=$(echo "$result" | tail -1)
    osascript -e "display notification \"$notification\" with title \"Sent to Kindle\""
else
    osascript -e "display alert \"Push to Kindle Failed\" message \"$result\" as critical"
fi
```

The script grabs the active tab URL from Brave automatically. If the tab is a **defuddle.md** URL, it extracts the already-rendered HTML from the live page and sends that — useful for articles (e.g. X/Twitter posts) that don't extract cleanly from their original URL. Otherwise it fetches and extracts as normal. Falls back to clipboard if Brave isn't frontmost.

## How it works

**URL path (normal articles):**
1. Fetches the page with `requests` / `trafilatura`
2. Extracts article content with `trafilatura`
3. Downloads and embeds images as base64 data URIs (skip with `--no-images`)
4. Wraps it in a minimal HTML document with readable typography
5. Sends it via Mail.app on macOS, or SMTP on Linux/Windows

**defuddle.md path (hard-to-extract articles, e.g. X/Twitter):**
1. Alfred detects the `https://defuddle.md/*` URL in the active Brave tab
2. Executes JavaScript in Brave to grab the already-rendered article HTML and page title
3. Writes the HTML to a temp file and calls `send_to_kindle.py --html-file ... --title ...`
4. Python wraps it in the same styled HTML document and sends it

> **Requires:** Brave → View > Developer → Allow JavaScript from Apple Events must be enabled.

## Requirements

- Python 3.9+
- macOS with Mail.app configured, **or** any platform with SMTP credentials
- Amazon Kindle with Personal Documents enabled

## License

MIT
