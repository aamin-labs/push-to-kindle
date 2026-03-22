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

result=$("$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/send_to_kindle.py" "$URL" 2>&1)

if [ $? -eq 0 ]; then
    notification=$(echo "$result" | tail -1)
    osascript -e "display notification \"$notification\" with title \"Sent to Kindle\""
else
    osascript -e "display alert \"Push to Kindle Failed\" message \"$result\" as critical"
fi
```

The script grabs the active tab URL from Brave automatically, with a fallback to clipboard content if Brave isn't the frontmost app.

## How it works

1. Fetches the page with `requests`
2. Extracts article content with `trafilatura`
3. Downloads and embeds images as base64 data URIs (skip with `--no-images`)
4. Wraps it in a minimal HTML document with readable typography
5. Sends it via Mail.app on macOS, or SMTP on Linux/Windows

## Requirements

- Python 3.9+
- macOS with Mail.app configured, **or** any platform with SMTP credentials
- Amazon Kindle with Personal Documents enabled

## License

MIT
