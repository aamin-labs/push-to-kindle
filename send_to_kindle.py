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
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from html import escape as html_escape
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote
from dotenv import load_dotenv
import requests
import trafilatura
from lxml import etree as letree
from lxml import html as lhtml

from app_helpers import bear_call, bear_callback_html, update_json_dict

load_dotenv(Path(__file__).parent / ".env")

KINDLE_EMAIL = os.getenv("KINDLE_EMAIL") or sys.exit("Error: KINDLE_EMAIL not set in .env")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


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


def _count_html_tags(content: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}\b", content, re.IGNORECASE))


def _unwrap_next_image(url: str) -> str:
    """Extract the real image URL from a Next.js /_next/image?url=... proxy URL."""
    if "/_next/image" in url:
        params = parse_qs(urlparse(url).query)
        if "url" in params:
            return unquote(params["url"][0])
    return url


def _pick_raw_content_node(raw_html: str):
    """Pick the most article-like node from the raw HTML."""
    tree = lhtml.fromstring(raw_html)
    class_matchers = [
        "w-richtext",
        "richtext",
        "rich-text",
        "article-body",
        "article-content",
        "post-content",
        "entry-content",
        "content-body",
        "prose",
    ]
    candidates = []
    seen = set()

    xpaths = [".//article", ".//main"]
    xpaths.extend(
        [
            (
                ".//*[contains("
                'concat(" ", normalize-space(@class), " "),'
                f' " {class_name} "'
                ")]"
            )
            for class_name in class_matchers
        ]
    )

    for xpath in xpaths:
        for node in tree.xpath(xpath):
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            text = " ".join(node.itertext()).strip()
            if len(text) < 400:
                continue
            score = len(text)
            score += len(node.xpath(".//p")) * 150
            score += len(node.xpath(".//li")) * 220
            score += len(node.xpath(".//img")) * 200
            score += len(node.xpath(".//h1|.//h2|.//h3")) * 120
            candidates.append((score, node))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return tree.find(".//article") or tree.find(".//main")


def _inline_to_html(node, base_url: str) -> str:
    parts = [html_escape(node.text or "")]
    for child in node:
        parts.append(_node_to_html(child, base_url))
    return "".join(parts)


def _node_to_html(node, base_url: str) -> str:
    tag = (node.tag or "").lower() if isinstance(node.tag, str) else ""
    tail = html_escape(node.tail or "")

    if not tag:
        return tail

    if tag in {"div", "section", "article", "main"}:
        return _inline_to_html(node, base_url) + tail
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return f"<{tag}>{_inline_to_html(node, base_url)}</{tag}>\n" + tail
    if tag == "p":
        return f"<p>{_inline_to_html(node, base_url)}</p>\n" + tail
    if tag == "blockquote":
        return f"<blockquote>{_inline_to_html(node, base_url)}</blockquote>\n" + tail
    if tag == "pre":
        text = html_escape("".join(node.itertext()))
        return f"<pre><code>{text}</code></pre>\n" + tail
    if tag == "code":
        return f"<code>{_inline_to_html(node, base_url)}</code>{tail}"
    if tag == "ul":
        items = "".join(_node_to_html(child, base_url) for child in node if getattr(child, "tag", None) == "li")
        return f"<ul>{items}</ul>\n" + tail
    if tag == "ol":
        items = "".join(_node_to_html(child, base_url) for child in node if getattr(child, "tag", None) == "li")
        return f"<ol>{items}</ol>\n" + tail
    if tag == "li":
        content = _inline_to_html(node, base_url).strip()
        return f"<li>{content}</li>\n" + tail if content else tail
    if tag in {"strong", "b"}:
        return f"<strong>{_inline_to_html(node, base_url)}</strong>{tail}"
    if tag in {"em", "i"}:
        return f"<em>{_inline_to_html(node, base_url)}</em>{tail}"
    if tag == "a":
        href = node.get("href", "").strip()
        if href:
            href = html_escape(urljoin(base_url, href), quote=True)
            return f'<a href="{href}">{_inline_to_html(node, base_url)}</a>{tail}'
        return _inline_to_html(node, base_url) + tail
    if tag == "br":
        return "<br/>\n" + tail
    if tag == "figure":
        inner = "".join(_node_to_html(child, base_url) for child in node)
        return f"<figure>{inner}</figure>\n" + tail if inner.strip() else tail
    if tag == "figcaption":
        return f"<figcaption>{_inline_to_html(node, base_url)}</figcaption>\n" + tail
    if tag == "img":
        src = node.get("srcset", "").strip()
        src = _pick_srcset_url(src) if src else node.get("src", "").strip()
        if not src or src.startswith("data:"):
            return tail
        abs_url = _strip_webp(_unwrap_next_image(urljoin(base_url, src)))
        if not abs_url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(abs_url):
            return tail
        alt = html_escape(node.get("alt", ""), quote=True)
        return f'<img src="{html_escape(abs_url, quote=True)}" alt="{alt}"/>' + tail
    if tag == "hr":
        return "<hr/>\n" + tail

    return _inline_to_html(node, base_url) + tail


def _node_to_markdown(node, base_url: str, list_depth: int = 0) -> str:
    tag = (node.tag or "").lower() if isinstance(node.tag, str) else ""
    tail = node.tail or ""

    if not tag:
        return tail

    if tag in {"div", "section", "article", "main"}:
        return "".join(_node_to_markdown(child, base_url, list_depth=list_depth) for child in node)
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag[1])
        inner = _markdown_inline(node, base_url).strip()
        return f'{"#" * level} {inner}\n\n' if inner else ""
    if tag == "p":
        inner = _markdown_inline(node, base_url).strip()
        return f"{inner}\n\n" if inner else ""
    if tag == "blockquote":
        inner = _markdown_inline(node, base_url).strip()
        if not inner:
            return ""
        lines = "\n".join(f"> {line}" for line in inner.splitlines())
        return f"{lines}\n\n"
    if tag == "pre":
        text = "".join(node.itertext()).strip("\n")
        return f"```\n{text}\n```\n\n" if text else ""
    if tag == "ul":
        return "".join(_node_to_markdown(child, base_url, list_depth=list_depth + 1) for child in node if getattr(child, "tag", None) == "li") + "\n"
    if tag == "ol":
        chunks = []
        for index, child in enumerate([c for c in node if getattr(c, "tag", None) == "li"], start=1):
            chunks.append(_node_to_markdown(child, base_url, list_depth=list_depth + 1).replace("- ", f"{index}. ", 1))
        return "".join(chunks) + "\n"
    if tag == "li":
        inner = _markdown_inline(node, base_url).strip()
        if not inner:
            return ""
        indent = "  " * max(list_depth - 1, 0)
        return f"{indent}- {inner}\n"
    if tag == "figure":
        return "".join(_node_to_markdown(child, base_url, list_depth=list_depth) for child in node)
    if tag == "img":
        src = node.get("srcset", "").strip()
        src = _pick_srcset_url(src) if src else node.get("src", "").strip()
        if not src or src.startswith("data:"):
            return ""
        abs_url = _strip_webp(_unwrap_next_image(urljoin(base_url, src)))
        if not abs_url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(abs_url):
            return ""
        alt = (node.get("alt", "") or "").strip()
        return f"![{alt}]({abs_url})\n\n"
    if tag == "hr":
        return "---\n\n"

    return _markdown_inline(node, base_url) + tail


def _markdown_inline(node, base_url: str) -> str:
    parts = [node.text or ""]
    for child in node:
        tag = (child.tag or "").lower() if isinstance(child.tag, str) else ""
        if tag in {"strong", "b"}:
            parts.append(f"**{_markdown_inline(child, base_url).strip()}**")
        elif tag in {"em", "i"}:
            parts.append(f"*{_markdown_inline(child, base_url).strip()}*")
        elif tag == "code":
            parts.append(f"`{''.join(child.itertext()).strip()}`")
        elif tag == "a":
            text = _markdown_inline(child, base_url).strip()
            href = child.get("href", "").strip()
            href = urljoin(base_url, href) if href else ""
            parts.append(f"[{text}]({href})" if text and href else text)
        elif tag == "br":
            parts.append("\n")
        elif tag in {"ul", "ol", "li", "p", "blockquote", "figure", "img"}:
            parts.append(_node_to_markdown(child, base_url).strip())
        else:
            parts.append(_markdown_inline(child, base_url))
        parts.append(child.tail or "")
    return "".join(parts)


def _extract_raw_preserved_content(raw_html: str, base_url: str) -> tuple[str, str]:
    """Extract minimally sanitized HTML and markdown from raw article markup."""
    node = _pick_raw_content_node(raw_html)
    if node is None:
        return "", ""

    html = "".join(_node_to_html(child, base_url) for child in node).strip()
    markdown = "".join(_node_to_markdown(child, base_url) for child in node).strip()
    return html, markdown


def _should_prefer_raw_content(extracted_html: str, raw_html_content: str) -> bool:
    """Prefer raw-preserved content when trafilatura drops key structure."""
    if not raw_html_content:
        return False

    extracted_text = re.sub(r"<[^>]+>", "", extracted_html or "")
    raw_text = re.sub(r"<[^>]+>", "", raw_html_content)
    if len(raw_text.strip()) < max(500, int(len(extracted_text.strip()) * 0.7)):
        return False

    extracted_has_lists = any(_count_html_tags(extracted_html, tag) for tag in ("ul", "ol", "li"))
    raw_has_lists = any(_count_html_tags(raw_html_content, tag) for tag in ("ul", "ol", "li"))
    extracted_has_images = _count_html_tags(extracted_html, "img") > 0
    raw_has_images = _count_html_tags(raw_html_content, "img") > 0

    return (raw_has_lists and not extracted_has_lists) or (raw_has_images and not extracted_has_images)


def _should_prefer_raw_markdown(extracted_markdown: str, raw_markdown: str) -> bool:
    if not raw_markdown:
        return False

    extracted_text = re.sub(r"[#*_`!\[\]\(\)-]", "", extracted_markdown or "")
    raw_text = re.sub(r"[#*_`!\[\]\(\)-]", "", raw_markdown)
    if len(raw_text.strip()) < max(500, int(len(extracted_text.strip()) * 0.7)):
        return False

    extracted_has_lists = bool(re.search(r"(?m)^\s*(?:[-*]|\d+\.)\s+", extracted_markdown or ""))
    raw_has_lists = bool(re.search(r"(?m)^\s*(?:[-*]|\d+\.)\s+", raw_markdown))
    extracted_has_images = bool(re.search(r"!\[[^\]]*\]\([^)]+\)", extracted_markdown or ""))
    raw_has_images = bool(re.search(r"!\[[^\]]*\]\([^)]+\)", raw_markdown))

    return (raw_has_lists and not extracted_has_lists) or (raw_has_images and not extracted_has_images)


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

    raw_preserved_html, raw_preserved_markdown = _extract_raw_preserved_content(raw_html, url)

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

    if _should_prefer_raw_content(content, raw_preserved_html):
        content = raw_preserved_html

    markdown = trafilatura.extract(
        raw_html, output_format="markdown", include_images=False,
        include_links=True, url=url,
    ) or ""

    if _should_prefer_raw_markdown(markdown, raw_preserved_markdown):
        markdown = raw_preserved_markdown

    return title, content, markdown


def fetch_defuddle_markdown(url: str) -> tuple[str, str, str]:
    """Fetch markdown from a defuddle.md URL.

    Returns (title, full_markdown_with_frontmatter, original_url).
    Raises ValueError if defuddle returns an error.
    """
    resp = requests.get(url, timeout=20, headers=_BROWSER_HEADERS)
    ct = resp.headers.get("Content-Type", "")
    if not ct.startswith("text/markdown"):
        try:
            data = resp.json()
            raise ValueError(f"defuddle.md error: {data.get('error', resp.text[:200])}")
        except (ValueError, KeyError):
            raise ValueError(f"defuddle.md returned unexpected content-type: {ct}")

    full_markdown = resp.text

    # Parse YAML frontmatter (between the two --- delimiters)
    frontmatter = ""
    parts = full_markdown.split("\n---\n", 2)
    if len(parts) >= 2 and parts[0].startswith("---"):
        frontmatter = parts[0][3:]  # strip leading ---

    def _fm_field(key: str) -> str:
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', frontmatter, re.MULTILINE)
        return m.group(1).strip() if m else ""

    title = _fm_field("title") or "Article"
    original_url = _fm_field("source")

    # Fallback: reconstruct original URL from defuddle URL path
    if not original_url:
        prefix = "https://defuddle.md/"
        path = url[len(prefix):]
        original_url = "https://" + path if "." in path else url

    return title, full_markdown, original_url


def markdown_to_epub(title: str, markdown: str) -> bytes:
    """Convert markdown (with YAML frontmatter) to EPUB bytes using pandoc.

    Raises RuntimeError if pandoc is not installed.
    """
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        raise RuntimeError("pandoc is not installed. Install with: brew install pandoc")

    md_file = None
    epub_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            f.write(markdown)
            md_file = f.name

        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as f:
            epub_file = f.name

        result = subprocess.run(
            ["pandoc", "--from", "markdown", "--to", "epub3", "-o", epub_file, md_file],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pandoc error: {result.stderr.decode().strip()}")

        return Path(epub_file).read_bytes()
    finally:
        if md_file:
            os.unlink(md_file)
        if epub_file:
            try:
                os.unlink(epub_file)
            except FileNotFoundError:
                pass


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


def send_attachment_via_smtp(
    title: str,
    attachment_bytes: bytes,
    mime_type: tuple[str, str],
    extension: str,
) -> None:
    """Send an attachment to Kindle via SMTP."""
    if not (SMTP_SERVER and SMTP_USER and SMTP_PASSWORD):
        sys.exit(
            "Error: SMTP_SERVER, SMTP_USER, and SMTP_PASSWORD must be set in .env."
        )

    safe_title = _safe_filename(title)

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = title
    msg.attach(MIMEText("Sent via push-to-kindle.", "plain"))

    part = MIMEBase(*mime_type)
    part.set_payload(attachment_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=f"{safe_title}.{extension}")
    msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, KINDLE_EMAIL, msg.as_string())

    print(f"Sent from: {SMTP_USER}  →  make sure this is on your Kindle approved list")


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


def create_bear_note(title: str, url: str, markdown_content: str = "") -> str | None:
    """Create a Bear note for a sent article. Returns the note identifier, or None on failure."""
    today = datetime.date.today().isoformat()
    body_parts = [today, "", url]
    if markdown_content:
        body_parts += ["", "---", "", markdown_content]
    note_body = "\n".join(body_parts)

    try:
        result = bear_call(
            "bear://x-callback-url/create"
            f"?title={quote(title)}"
            f"&text={quote(note_body)}"
            f"&tags={quote('0a/reading')}"
        )
        return result.get("identifier") if result else None
    except Exception as e:
        print(f"Warning: Bear note creation failed: {e}", file=sys.stderr)
        return None


def _bear_callback_html() -> str:
    return bear_callback_html()


def _extract_snippet(markdown: str, max_chars: int = 200) -> str:
    """Return the first two non-empty, non-heading lines of markdown as a snippet."""
    lines = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
        if len(lines) == 2:
            break
    snippet = " ".join(lines)
    return snippet[:max_chars] + ("…" if len(snippet) > max_chars else "")


def save_snippet(title: str, markdown: str) -> None:
    """Append {title: snippet} to ~/logs/kindle-snippets.json."""
    if not markdown:
        return
    snippet = _extract_snippet(markdown)
    if not snippet:
        return
    update_json_dict(Path.home() / "logs" / "kindle-snippets.json", title, snippet)


def save_to_kindle_bear_map(title: str, note_id: str) -> None:
    """Append {title: note_id} to ~/logs/kindle-bear-map.json."""
    update_json_dict(Path.home() / "logs" / "kindle-bear-map.json", title, note_id)


def _rewrite_x_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"x.com", "www.x.com"}:
        return url
    return f"https://defuddle.md/{host}{parsed.path}"


def _sync_bear_note(title: str, source_url: str, markdown: str) -> None:
    if sys.platform != "darwin":
        return

    note_id = create_bear_note(title, source_url, markdown)
    if note_id:
        save_to_kindle_bear_map(title, note_id)
    else:
        print("Warning: Bear note not created", file=sys.stderr)


def _handle_html_file(path: str, title_override: str | None, dry_run_mode: bool) -> None:
    print(f"Reading: {path}")
    title, content = convert_html_file(path, title_override=title_override)
    html = wrap_html(title, content)

    if dry_run_mode:
        dry_run(title, html)
        return

    send_attachment_via_smtp(title, html.encode("utf-8"), ("text", "html"), "html")
    print(f"Sent to Kindle: {title}")


def _handle_defuddle_url(url: str, dry_run_mode: bool) -> None:
    print(f"Fetching via defuddle.md: {url}")
    title, markdown, original_url = fetch_defuddle_markdown(url)
    print(f"Extracted: {title!r}")

    if dry_run_mode:
        safe_title = _safe_filename(title)
        out_path = Path(f"{safe_title}.md").resolve()
        out_path.write_text(markdown, encoding="utf-8")
        print(f"Dry run — saved markdown to: {out_path}")
        return

    print("Converting to EPUB...")
    epub_bytes = markdown_to_epub(title, markdown)
    send_attachment_via_smtp(title, epub_bytes, ("application", "epub+zip"), "epub")
    save_snippet(title, markdown)
    _sync_bear_note(title, original_url, markdown)
    print(f"Sent to Kindle: {title}")


def _handle_article_url(url: str, include_images: bool, dry_run_mode: bool) -> None:
    print(f"Fetching: {url}")
    title, content, markdown = fetch_article(url, include_images=include_images)
    print(f"Extracted: {title!r}")

    html = wrap_html(title, content)
    if dry_run_mode:
        dry_run(title, html)
        return

    send_attachment_via_smtp(title, html.encode("utf-8"), ("text", "html"), "html")
    save_snippet(title, markdown)
    _sync_bear_note(title, url, markdown)
    print(f"Sent to Kindle: {title}")


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
            _handle_html_file(args.html_file, args.title, args.dry_run)
        else:
            url = _rewrite_x_url(args.url)
            if url.startswith("https://defuddle.md/"):
                _handle_defuddle_url(url, args.dry_run)
            else:
                _handle_article_url(url, include_images=not args.no_images, dry_run_mode=args.dry_run)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
