# push-to-kindle

Send any web article to your Kindle in one command. Extracts the core content, strips clutter, and delivers it as a clean HTML document via your email.

## Installation

```bash
git clone https://github.com/your-username/push-to-kindle.git
cd push-to-kindle
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```
KINDLE_EMAIL=yourname@kindle.com
```

**Prerequisites:**

1. Add your sending email to Amazon's approved senders list:
   Amazon account → Manage Your Content and Devices → Preferences → Personal Document Settings → Approved Personal Document E-mail List

2. Mail.app must be configured with the email account you want to send from.

## Usage

```bash
python3 send_to_kindle.py "https://example.com/article"
```

The article is extracted, wrapped in a clean HTML document, and emailed to your Kindle via Mail.app. It appears on your device within a minute or two.

**Alfred integration:** Add a Run Script workflow action:

```bash
cd /path/to/push-to-kindle && .venv/bin/python3 send_to_kindle.py "{query}"
```

Trigger it with a copied URL as the input.

## How it works

1. Fetches the page with `requests`
2. Extracts article content with `trafilatura`
3. Wraps it in a minimal HTML document with readable typography
4. Sends it to your Kindle email via Mail.app (AppleScript)

## Requirements

- Python 3.9+
- macOS with Mail.app configured
- Amazon Kindle with Personal Documents enabled

## License

MIT
