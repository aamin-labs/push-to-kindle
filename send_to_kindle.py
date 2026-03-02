#!/usr/bin/env python3
"""Send a web article to Kindle via Amazon's Personal Documents Service."""

import sys
import os
import re
import textwrap
import tempfile
import subprocess
import smtplib
import argparse
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from dotenv import load_dotenv
import requests
import trafilatura

load_dotenv(Path(__file__).parent / ".env")

KINDLE_EMAIL = os.getenv("KINDLE_EMAIL") or sys.exit("Error: KINDLE_EMAIL not set in .env")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")  # optional: pin a specific Mail.app account
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


def use_smtp() -> bool:
    """True if SMTP should be used: non-macOS platform, or SMTP config is present."""
    if sys.platform != "darwin":
        return True
    return bool(SMTP_SERVER and SMTP_USER and SMTP_PASSWORD)


def fetch_article(url: str) -> tuple[str, str]:
    """Fetch and extract article content. Returns (title, html_content)."""
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    html = response.text

    metadata = trafilatura.bare_extraction(html, url=url)
    title = metadata.title if metadata else None
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        title = m.group(1).strip() if m else "Article"

    content = trafilatura.extract(
        html,
        output_format="html",
        include_images=False,
        include_links=False,
        url=url,
    )
    if not content:
        raise ValueError("Could not extract article content from page.")

    return title, content


def wrap_html(title: str, content: str) -> str:
    """Wrap extracted HTML in a minimal full document with readable styling."""
    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>{title}</title>
          <style>
            body {{ font-family: Georgia, serif; line-height: 1.6; max-width: 680px;
                   margin: 2em auto; padding: 0 1em; color: #111; }}
            h1 {{ font-size: 1.6em; margin-bottom: 0.3em; }}
            h2, h3 {{ margin-top: 1.4em; }}
            p {{ margin: 0.8em 0; }}
            blockquote {{ border-left: 3px solid #ccc; margin: 1em 0; padding-left: 1em;
                          color: #555; }}
            pre, code {{ font-family: monospace; background: #f4f4f4; padding: 0.2em 0.4em; }}
          </style>
        </head>
        <body>
          <h1>{title}</h1>
          {content}
        </body>
        </html>
    """)


def send_via_smtp(title: str, html: str) -> None:
    """Send the HTML document via SMTP."""
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASSWORD):
        sys.exit(
            "Error: SMTP_SERVER, SMTP_USER, and SMTP_PASSWORD must be set in .env "
            "(required on non-macOS systems)."
        )

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = title
    msg.attach(MIMEText("Sent via push-to-kindle.", "plain"))

    part = MIMEBase("text", "html")
    part.set_payload(html.encode("utf-8"))
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=f"{safe_title}.html")
    msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, KINDLE_EMAIL, msg.as_string())

    print(f"Sent from: {SMTP_USER}  →  make sure this is on your Kindle approved list")


def send_via_mail_app(title: str, html: str) -> None:
    """Send the HTML document via Mail.app using AppleScript (macOS only)."""
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])

    with tempfile.NamedTemporaryFile(
        suffix=".html", prefix=safe_title + "_", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html)
        tmp_path = f.name

    try:
        escaped_title = title.replace('"', '\\"')
        sender_prop = f', sender:"{SENDER_EMAIL}"' if SENDER_EMAIL else ""
        script = f"""
        set theFile to (POSIX file "{tmp_path}") as alias
        tell application "Mail"
            set m to make new outgoing message with properties {{subject:"{escaped_title}", content:" ", visible:false{sender_prop}}}
            tell m
                make new to recipient with properties {{address:"{KINDLE_EMAIL}"}}
                make new attachment with properties {{file name:theFile}} at after last paragraph of content of m
                send
            end tell
        end tell
        """
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Mail.app error: {result.stderr.strip()}")
    finally:
        os.unlink(tmp_path)

    sender_display = SENDER_EMAIL or "Mail.app default account"
    print(f"Sent from: {sender_display}  →  make sure this is on your Kindle approved list")


def dry_run(title: str, html: str) -> None:
    """Save the extracted HTML locally for inspection without sending."""
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])
    out_path = Path(f"{safe_title}.html").resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"Dry run — saved to: {out_path}")
    print("Open the file to preview how it will appear on Kindle.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a web article to your Kindle.")
    parser.add_argument("url", help="URL of the article to send")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and save HTML locally without sending",
    )
    args = parser.parse_args()

    print(f"Fetching: {args.url}")
    title, content = fetch_article(args.url)
    print(f"Extracted: {title!r}")

    html = wrap_html(title, content)

    if args.dry_run:
        dry_run(title, html)
        return

    if use_smtp():
        send_via_smtp(title, html)
    else:
        send_via_mail_app(title, html)

    print(f"Sent to Kindle: {title}")


if __name__ == "__main__":
    main()
