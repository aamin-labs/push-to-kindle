#!/usr/bin/env python3
"""Send a web article to Kindle via Amazon's Personal Documents Service."""

import sys
import os
import re
import textwrap
import tempfile
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import requests
import trafilatura

load_dotenv(Path(__file__).parent / ".env")

KINDLE_EMAIL = os.getenv("KINDLE_EMAIL") or sys.exit("Error: KINDLE_EMAIL not set in .env")


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


def send_via_mail_app(title: str, html: str, kindle_email: str) -> None:
    """Write HTML to a temp file and send it via Mail.app using AppleScript."""
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])

    with tempfile.NamedTemporaryFile(
        suffix=".html", prefix=safe_title + "_", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html)
        tmp_path = f.name

    try:
        escaped_title = title.replace('"', '\\"')
        script = f"""
        set theFile to (POSIX file "{tmp_path}") as alias
        tell application "Mail"
            set m to make new outgoing message with properties {{subject:"{escaped_title}", content:" ", visible:false}}
            tell m
                make new to recipient with properties {{address:"{kindle_email}"}}
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


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: send_to_kindle.py <url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1].strip()
    print(f"Fetching: {url}")

    title, content = fetch_article(url)
    print(f"Extracted: {title!r}")

    html = wrap_html(title, content)
    send_via_mail_app(title, html, KINDLE_EMAIL)

    print(f"Sent to Kindle: {title}")


if __name__ == "__main__":
    main()
