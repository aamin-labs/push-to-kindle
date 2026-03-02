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

# Preview extraction locally without sending
python3 send_to_kindle.py --dry-run "https://example.com/article"
```

The article is extracted, wrapped in a clean HTML document, and delivered to your Kindle. It appears on your device within a minute or two.

## How it works

1. Fetches the page with `requests`
2. Extracts article content with `trafilatura`
3. Wraps it in a minimal HTML document with readable typography
4. Sends it via Mail.app on macOS, or SMTP on Linux/Windows

## Requirements

- Python 3.9+
- macOS with Mail.app configured, **or** any platform with SMTP credentials
- Amazon Kindle with Personal Documents enabled

## License

MIT
