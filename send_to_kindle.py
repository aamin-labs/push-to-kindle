#!/usr/bin/env python3
"""Send a web article to Kindle via Amazon's Personal Documents Service."""

import sys
import os
import re
import base64
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
from html import escape as html_escape
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from dotenv import load_dotenv
import requests
import trafilatura
from lxml import etree as letree
from lxml import html as lhtml

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


_SKIP_IMAGE = re.compile(r"(placeholder|tracking|pixel|spacer|\.svg)", re.IGNORECASE)


def _pick_srcset_url(srcset: str) -> str:
    """Return the largest-width URL from a srcset string."""
    best_url, best_w = "", 0
    for entry in srcset.split(","):
        parts = entry.strip().split()
        if not parts:
            continue
        url = parts[0]
        w = 0
        if len(parts) > 1 and parts[1].endswith("w"):
            try:
                w = int(parts[1][:-1])
            except ValueError:
                pass
        if w > best_w or not best_url:
            best_w, best_url = w, url
    return best_url


def _strip_webp(url: str) -> str:
    """Convert .jpg.webp / .png.webp → .jpg / .png (Kindle doesn't support WebP)."""
    if url.lower().endswith(".webp"):
        base = url[:-5]
        if re.search(r"\.(jpe?g|png|gif)$", base, re.IGNORECASE):
            return base
    return url


def _unwrap_next_image(url: str) -> str:
    """Extract the real image URL from a Next.js /_next/image?url=... proxy URL."""
    if "/_next/image" in url:
        params = parse_qs(urlparse(url).query)
        if "url" in params:
            return unquote(params["url"][0])
    return url


def _xml_to_html(xml_str: str, base_url: str) -> str:
    """Convert trafilatura XML to HTML, resolving image URLs in their original positions."""
    try:
        root = letree.fromstring(xml_str.encode())
    except letree.XMLSyntaxError:
        return ""

    def convert(el) -> str:
        tag = el.tag
        text = html_escape(el.text or "")
        tail = html_escape(el.tail or "")
        children = "".join(convert(c) for c in el)
        inner = text + children

        if tag in ("doc", "main", "comments", "header", "footer"):
            return inner + tail
        elif tag == "head":
            level = el.get("rend", "h2")
            if level not in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = "h2"
            return f"<{level}>{inner}</{level}>\n{tail}"
        elif tag == "p":
            return f"<p>{inner}</p>\n{tail}"
        elif tag == "list":
            lt = "ol" if el.get("rend") == "ol" else "ul"
            return f"<{lt}>{inner}</{lt}>\n{tail}"
        elif tag == "item":
            return f"<li>{inner}</li>\n{tail}"
        elif tag in ("quote", "abstract"):
            return f"<blockquote>{inner}</blockquote>\n{tail}"
        elif tag == "code":
            return f"<pre><code>{inner}</code></pre>\n{tail}"
        elif tag == "hi":
            rend = el.get("rend", "")
            if "bold" in rend:
                return f"<strong>{inner}</strong>{tail}"
            elif "italic" in rend:
                return f"<em>{inner}</em>{tail}"
            return inner + tail
        elif tag == "graphic":
            src = el.get("src", "")
            if not src:
                return tail
            abs_url = _unwrap_next_image(urljoin(base_url, src))
            abs_url = _strip_webp(abs_url)
            if not abs_url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(abs_url):
                return tail
            return f'<figure><img src="{abs_url}" alt=""></figure>\n{tail}'
        else:
            return inner + tail

    return convert(root)


def _embed_img_srcs(content: str) -> tuple[str, int]:
    """Download and base64-embed all <img src="http..."> URLs already in content."""
    count = 0

    def replace_src(match: re.Match) -> str:
        nonlocal count
        url = match.group(1)
        try:
            resp = requests.get(url, timeout=10, headers=_BROWSER_HEADERS)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if not ct.startswith("image/") or ct == "image/webp":
                return match.group(0)
            b64 = base64.b64encode(resp.content).decode("ascii")
            count += 1
            return f'src="data:{ct};base64,{b64}"'
        except Exception:
            return match.group(0)

    result = re.sub(r'src="(https?://[^"]+)"', replace_src, content)
    return result, count


def _prepend_images_from_raw(content: str, raw_html: str, base_url: str) -> str:
    """Fallback: extract images from article container in raw HTML and prepend."""
    tree = lhtml.fromstring(raw_html)
    container = tree.find(".//article") or tree.find(".//main") or tree
    scoped_html = lhtml.tostring(container, encoding="unicode")

    seen: set[str] = set()
    img_urls: list[str] = []

    for tag_m in re.finditer(r"<img\b[^>]*/?>", scoped_html, re.IGNORECASE):
        img_html = tag_m.group(0)
        srcset_m = re.search(r'srcset="([^"]+)"', img_html, re.IGNORECASE)
        if srcset_m:
            url = _pick_srcset_url(srcset_m.group(1))
        else:
            src_m = re.search(r'src="([^"]+)"', img_html, re.IGNORECASE)
            if not src_m:
                continue
            url = src_m.group(1)

        if not url or url.startswith("data:"):
            continue
        abs_url = _unwrap_next_image(urljoin(base_url, url))
        abs_url = _strip_webp(abs_url)
        if not abs_url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(abs_url):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            img_urls.append(abs_url)

    embedded = []
    for url in img_urls:
        try:
            resp = requests.get(url, timeout=10, headers=_BROWSER_HEADERS)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if not ct.startswith("image/") or ct == "image/webp":
                continue
            b64 = base64.b64encode(resp.content).decode("ascii")
            embedded.append(f'<figure><img src="data:{ct};base64,{b64}" alt=""></figure>')
        except Exception:
            continue

    if not embedded:
        return content
    print(f"  Embedded {len(embedded)} image(s) (prepended)")
    return "\n".join(embedded) + "\n" + content


def _safe_filename(title: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_article(url: str, include_images: bool = True) -> tuple[str, str]:
    """Fetch and extract article content. Returns (title, html_content)."""
    # Try trafilatura's fetcher first (better anti-bot handling), fall back to requests
    raw_html = trafilatura.fetch_url(url)
    if not raw_html:
        response = requests.get(url, timeout=15, headers=_BROWSER_HEADERS)
        response.raise_for_status()
        raw_html = response.text

    metadata = trafilatura.bare_extraction(raw_html, url=url)
    title = metadata.title if metadata else None
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", raw_html, re.IGNORECASE)
        title = m.group(1).strip() if m else "Article"

    if include_images:
        # XML mode preserves <graphic> positions — convert to HTML with img srcs in place
        xml = trafilatura.extract(
            raw_html, output_format="xml", include_images=True, include_links=False, url=url
        )
        content = _xml_to_html(xml, url) if xml else ""
        if content:
            print("Embedding images...")
            content, count = _embed_img_srcs(content)
            if count:
                print(f"  Embedded {count} image(s) in position")
            elif not content.strip():
                content = ""  # force fallback

        if not content:
            # Fallback: trafilatura HTML + prepend images from raw HTML
            content = trafilatura.extract(
                raw_html, output_format="html", include_images=False,
                include_links=False, url=url,
            ) or ""
            if content:
                print("Embedding images...")
                content = _prepend_images_from_raw(content, raw_html, url)
    else:
        content = trafilatura.extract(
            raw_html, output_format="html", include_images=False,
            include_links=False, url=url,
        ) or ""

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
            img {{ max-width: 100%; height: auto; display: block; margin: 1em auto; }}
            figure {{ margin: 1em 0; }}
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

    safe_title = _safe_filename(title)

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
    safe_title = _safe_filename(title)

    with tempfile.NamedTemporaryFile(
        suffix=".html", prefix=safe_title + "_", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html)
        tmp_path = f.name

    try:
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
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
    safe_title = _safe_filename(title)
    out_path = Path(f"{safe_title}.html").resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"Dry run — saved to: {out_path}")
    print("Open the file to preview how it will appear on Kindle.")


def convert_html_file(path: str, title_override: str | None = None) -> tuple[str, str]:
    """Read a rendered HTML fragment from a file. Returns (title, html_content)."""
    content = Path(path).read_text(encoding="utf-8").strip()

    if title_override:
        title = title_override
    else:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else "Article"

    return title, content


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a web article to your Kindle.")
    parser.add_argument("url", nargs="?", help="URL of the article to send")
    parser.add_argument(
        "--html-file",
        metavar="PATH",
        help="Send a pre-rendered HTML fragment file instead of fetching a URL",
    )
    parser.add_argument(
        "--title",
        metavar="TITLE",
        help="Override the article title (useful with --html-file)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and save HTML locally without sending",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip downloading and embedding images",
    )
    args = parser.parse_args()

    if not args.html_file and not args.url:
        parser.error("provide a URL or --html-file PATH")

    try:
        if args.html_file:
            print(f"Reading: {args.html_file}")
            title, content = convert_html_file(args.html_file, title_override=args.title)
        else:
            print(f"Fetching: {args.url}")
            include_images = not args.no_images
            title, content = fetch_article(args.url, include_images=include_images)
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
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
