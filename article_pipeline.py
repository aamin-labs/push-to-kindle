"""Article extraction and repair workflows."""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse


_SKIP_IMAGE = re.compile(r"(placeholder|tracking|pixel|spacer|\.svg)", re.IGNORECASE)
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _requests_module():
    import requests

    return requests


def _trafilatura_module():
    import trafilatura

    return trafilatura


def _lxml_html_module():
    from lxml import html as lhtml

    return lhtml


def _lxml_etree_module():
    from lxml import etree as letree

    return letree


@dataclass
class ExtractedArticle:
    title: str
    source_url: str
    html_content: str = ""
    markdown_content: str = ""
    delivery_format: str = "html"


def rewrite_x_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"x.com", "www.x.com"}:
        return url
    return f"https://defuddle.md/{host}{parsed.path}"


def _pick_srcset_url(srcset: str) -> str:
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
    if url.lower().endswith(".webp"):
        base = url[:-5]
        if re.search(r"\.(jpe?g|png|gif)$", base, re.IGNORECASE):
            return base
    return url


def _count_html_tags(content: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}\b", content, re.IGNORECASE))


def _unwrap_next_image(url: str) -> str:
    if "/_next/image" in url:
        params = parse_qs(urlparse(url).query)
        if "url" in params:
            return unquote(params["url"][0])
    return url


def _pick_raw_content_node(raw_html: str):
    lhtml = _lxml_html_module()
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
        items = [c for c in node if getattr(c, "tag", None) == "li"]
        for index, child in enumerate(items, start=1):
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


def extract_raw_preserved_content(raw_html: str, base_url: str) -> tuple[str, str]:
    node = _pick_raw_content_node(raw_html)
    if node is None:
        return "", ""
    html = "".join(_node_to_html(child, base_url) for child in node).strip()
    markdown = "".join(_node_to_markdown(child, base_url) for child in node).strip()
    return html, markdown


def should_prefer_raw_content(extracted_html: str, raw_html_content: str) -> bool:
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


def should_prefer_raw_markdown(extracted_markdown: str, raw_markdown: str) -> bool:
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
    letree = _lxml_etree_module()
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
        if tag == "head":
            level = el.get("rend", "h2")
            if level not in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = "h2"
            return f"<{level}>{inner}</{level}>\n{tail}"
        if tag == "p":
            return f"<p>{inner}</p>\n{tail}"
        if tag == "list":
            list_tag = "ol" if el.get("rend") == "ol" else "ul"
            return f"<{list_tag}>{inner}</{list_tag}>\n{tail}"
        if tag == "item":
            return f"<li>{inner}</li>\n{tail}"
        if tag in ("quote", "abstract"):
            return f"<blockquote>{inner}</blockquote>\n{tail}"
        if tag == "code":
            return f"<pre><code>{inner}</code></pre>\n{tail}"
        if tag == "hi":
            rend = el.get("rend", "")
            if "bold" in rend:
                return f"<strong>{inner}</strong>{tail}"
            if "italic" in rend:
                return f"<em>{inner}</em>{tail}"
            return inner + tail
        if tag == "graphic":
            src = el.get("src", "")
            if not src:
                return tail
            abs_url = _unwrap_next_image(urljoin(base_url, src))
            abs_url = _strip_webp(abs_url)
            if not abs_url.startswith(("http://", "https://")) or _SKIP_IMAGE.search(abs_url):
                return tail
            return f'<figure><img src="{abs_url}" alt=""></figure>\n{tail}'
        return inner + tail

    return convert(root)


def _embed_img_srcs(content: str) -> tuple[str, int]:
    requests = _requests_module()
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
    requests = _requests_module()
    lhtml = _lxml_html_module()
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


def safe_filename(title: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in title[:80])


class ArticleExtractor:
    def extract_url(self, url: str, *, include_images: bool = True) -> ExtractedArticle:
        trafilatura = _trafilatura_module()
        requests = _requests_module()

        raw_html = trafilatura.fetch_url(url)
        if not raw_html:
            response = requests.get(url, timeout=15, headers=_BROWSER_HEADERS)
            response.raise_for_status()
            raw_html = response.text

        metadata = trafilatura.bare_extraction(raw_html, url=url)
        title = metadata.title if metadata else None
        if not title:
            match = re.search(r"<title[^>]*>([^<]+)</title>", raw_html, re.IGNORECASE)
            title = match.group(1).strip() if match else "Article"

        raw_html_content, raw_markdown = extract_raw_preserved_content(raw_html, url)

        if include_images:
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
                    content = ""
            if not content:
                content = trafilatura.extract(
                    raw_html, output_format="html", include_images=False, include_links=False, url=url
                ) or ""
                if content:
                    print("Embedding images...")
                    content = _prepend_images_from_raw(content, raw_html, url)
        else:
            content = trafilatura.extract(
                raw_html, output_format="html", include_images=False, include_links=False, url=url
            ) or ""

        if not content:
            raise ValueError("Could not extract article content from page.")
        if should_prefer_raw_content(content, raw_html_content):
            content = raw_html_content

        markdown = trafilatura.extract(
            raw_html, output_format="markdown", include_images=False, include_links=True, url=url
        ) or ""
        if should_prefer_raw_markdown(markdown, raw_markdown):
            markdown = raw_markdown

        return ExtractedArticle(
            title=title,
            source_url=url,
            html_content=content,
            markdown_content=markdown,
            delivery_format="html",
        )

    def extract_defuddle(self, url: str) -> ExtractedArticle:
        requests = _requests_module()
        resp = requests.get(url, timeout=20, headers=_BROWSER_HEADERS)
        ct = resp.headers.get("Content-Type", "")
        if not ct.startswith("text/markdown"):
            try:
                data = resp.json()
                raise ValueError(f"defuddle.md error: {data.get('error', resp.text[:200])}")
            except (ValueError, KeyError):
                raise ValueError(f"defuddle.md returned unexpected content-type: {ct}")

        full_markdown = resp.text
        frontmatter = ""
        parts = full_markdown.split("\n---\n", 2)
        if len(parts) >= 2 and parts[0].startswith("---"):
            frontmatter = parts[0][3:]

        def field(key: str) -> str:
            match = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', frontmatter, re.MULTILINE)
            return match.group(1).strip() if match else ""

        title = field("title") or "Article"
        original_url = field("source")
        if not original_url:
            prefix = "https://defuddle.md/"
            path = url[len(prefix):]
            original_url = "https://" + path if "." in path else url

        return ExtractedArticle(
            title=title,
            source_url=original_url,
            markdown_content=full_markdown,
            delivery_format="epub",
        )

    def read_html_file(self, path: str, *, title_override: str | None = None) -> ExtractedArticle:
        content = Path(path).read_text(encoding="utf-8").strip()
        if title_override:
            title = title_override
        else:
            match = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else "Article"
        return ExtractedArticle(title=title, source_url="", html_content=content, delivery_format="html")


def markdown_to_epub(title: str, markdown: str) -> bytes:
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("pandoc is not installed. Install with: brew install pandoc") from exc

    md_file = None
    epub_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as handle:
            handle.write(markdown)
            md_file = handle.name
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as handle:
            epub_file = handle.name

        result = subprocess.run(
            ["pandoc", "--from", "markdown", "--to", "epub3", "-o", epub_file, md_file],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pandoc error: {result.stderr.decode().strip()}")

        return Path(epub_file).read_bytes()
    finally:
        if md_file:
            Path(md_file).unlink(missing_ok=True)
        if epub_file:
            Path(epub_file).unlink(missing_ok=True)
