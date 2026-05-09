"""Workflow for preparing and delivering articles to Kindle."""

from __future__ import annotations

import datetime
import mimetypes
import os
import re
import smtplib
import sys
import textwrap
from dataclasses import dataclass
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape
from pathlib import Path

from article_pipeline import ExtractedArticle, markdown_to_epub, safe_filename
from app_helpers import BearClient, JsonDictStore


@dataclass
class DeliveryResult:
    title: str
    delivered_format: str
    output_path: str | None = None
    bear_note_id: str | None = None


@dataclass
class FileAttachment:
    filename: str
    attachment_bytes: bytes
    mime_type: tuple[str, str]
    extension: str


class ArticleMetadataStore:
    def __init__(
        self,
        json_store: JsonDictStore | None = None,
        snippets_path: Path | None = None,
        bear_map_path: Path | None = None,
    ):
        self._json_store = json_store or JsonDictStore()
        self._snippets_path = snippets_path or (Path.home() / "logs" / "kindle-snippets.json")
        self._bear_map_path = bear_map_path or (Path.home() / "logs" / "kindle-bear-map.json")

    def save_snippet(self, title: str, markdown: str) -> None:
        if not markdown:
            return
        snippet = self._extract_snippet(markdown)
        if not snippet:
            return
        self._json_store.update(self._snippets_path, title, snippet)

    def save_bear_note_mapping(self, title: str, note_id: str) -> None:
        self._json_store.update(self._bear_map_path, title, note_id)

    @staticmethod
    def _extract_snippet(markdown: str, max_chars: int = 200) -> str:
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


class SmtpSender:
    def __init__(
        self,
        *,
        kindle_email: str,
        smtp_server: str | None,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
    ):
        self.kindle_email = kindle_email
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password

    def send_attachment(
        self,
        title: str,
        attachment_bytes: bytes,
        mime_type: tuple[str, str],
        extension: str,
        *,
        filename: str | None = None,
    ) -> None:
        if not self.kindle_email:
            raise RuntimeError("KINDLE_EMAIL must be set in .env.")
        if not (self.smtp_server and self.smtp_user and self.smtp_password):
            raise RuntimeError("SMTP_SERVER, SMTP_USER, and SMTP_PASSWORD must be set in .env.")

        msg = MIMEMultipart()
        msg["From"] = self.smtp_user
        msg["To"] = self.kindle_email
        msg["Subject"] = title
        msg.attach(MIMEText("Sent via push-to-kindle.", "plain"))

        part = MIMEBase(*mime_type)
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        attachment_filename = filename or f"{safe_filename(title)}.{extension}"
        part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(part)

        with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, self.kindle_email, msg.as_string())

        print(f"Sent from: {self.smtp_user}  →  make sure this is on your Kindle approved list")


class KindleDeliveryService:
    def __init__(
        self,
        extractor,
        smtp_sender: SmtpSender,
        metadata_store: ArticleMetadataStore | None = None,
        bear_client: BearClient | None = None,
        *,
        platform: str | None = None,
        epub_converter=markdown_to_epub,
    ):
        self._extractor = extractor
        self._smtp_sender = smtp_sender
        self._metadata_store = metadata_store or ArticleMetadataStore()
        self._bear_client = bear_client or BearClient()
        self._platform = platform or sys.platform
        self._epub_converter = epub_converter

    def deliver_url(
        self,
        url: str,
        *,
        include_images: bool = True,
        dry_run: bool = False,
        save_to_bear: bool = False,
    ) -> DeliveryResult:
        print(f"Fetching: {url}")
        article = self._extractor.prepare_for_kindle(url, include_images=include_images)
        print(f"Extracted: {article.title!r}")
        return self._deliver(article, dry_run=dry_run, save_to_bear=save_to_bear)

    def deliver_html_file(
        self,
        path: str,
        *,
        title_override: str | None = None,
        dry_run: bool = False,
        save_to_bear: bool = False,
    ) -> DeliveryResult:
        print(f"Reading: {path}")
        article = self._extractor.prepare_local_html(path, title_override=title_override)
        return self._deliver(article, dry_run=dry_run, save_to_bear=save_to_bear)

    def deliver_file(self, path: str, *, dry_run: bool = False) -> DeliveryResult:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        title = file_path.stem
        attachment = _prepare_file_attachment(file_path)
        if dry_run:
            print(f"Dry run — would send file to Kindle: {file_path}")
            return DeliveryResult(title=title, delivered_format="dry-run", output_path=str(file_path))

        print(f"Sending file: {file_path}")
        self._smtp_sender.send_attachment(
            title,
            attachment.attachment_bytes,
            attachment.mime_type,
            attachment.extension,
            filename=attachment.filename,
        )
        print(f"Sent to Kindle: {attachment.filename}")
        return DeliveryResult(title=title, delivered_format="file", output_path=str(file_path))

    def _deliver(self, article: ExtractedArticle, *, dry_run: bool, save_to_bear: bool) -> DeliveryResult:
        if article.delivery_format == "epub":
            if dry_run:
                out_path = self._write_preview(article.title, article.markdown_content, "md")
                return DeliveryResult(title=article.title, delivered_format="dry-run", output_path=str(out_path))

            print("Converting to EPUB...")
            payload = self._epub_converter(article.title, article.markdown_content, article.author)
            self._smtp_sender.send_attachment(article.title, payload, ("application", "epub+zip"), "epub")
        else:
            html = wrap_html(article.title, article.html_content, article.author)
            if dry_run:
                out_path = self._write_preview(article.title, html, "html")
                return DeliveryResult(title=article.title, delivered_format="dry-run", output_path=str(out_path))

            self._smtp_sender.send_attachment(article.title, html.encode("utf-8"), ("text", "html"), "html")

        note_id = None
        self._metadata_store.save_snippet(article.title, article.markdown_content)
        if save_to_bear and article.source_url and self._platform == "darwin":
            note_id = self._sync_bear_note(article.title, article.source_url, article.markdown_content)
        print(f"Sent to Kindle: {article.title}")
        return DeliveryResult(
            title=article.title,
            delivered_format=article.delivery_format,
            bear_note_id=note_id,
        )

    def _sync_bear_note(self, title: str, source_url: str, markdown: str) -> str | None:
        today = datetime.date.today().isoformat()
        body_parts = [today, "", source_url]
        if markdown:
            body_parts += ["", "---", "", markdown]
        note_body = "\n".join(body_parts)
        try:
            result = self._bear_client.create_note(title=title, text=note_body, tags="0a/reading")
            note_id = result.get("identifier") if result else None
            if note_id:
                self._metadata_store.save_bear_note_mapping(title, note_id)
                return note_id
            print("Warning: Bear note not created", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"Warning: Bear note creation failed: {exc}", file=sys.stderr)
            return None

    @staticmethod
    def _write_preview(title: str, content: str, extension: str) -> Path:
        out_path = Path(f"{safe_filename(title)}.{extension}").resolve()
        out_path.write_text(content, encoding="utf-8")
        if extension == "html":
            print(f"Dry run — saved to: {out_path}")
            print("Open the file to preview how it will appear on Kindle.")
        else:
            print(f"Dry run — saved markdown to: {out_path}")
        return out_path


def wrap_html(title: str, content: str, author: str = "") -> str:
    escaped_title = html_escape(title)
    escaped_author = html_escape(author)
    author_meta = (
        f'<meta name="author" content="{escaped_author}">\n          '
        f'<meta name="dc.creator" content="{escaped_author}">'
        if author
        else ""
    )
    return textwrap.dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>{escaped_title}</title>
          {author_meta}
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
          <h1>{escaped_title}</h1>
          {content}
        </body>
        </html>
        """
    )


def _guess_mime_type(path: Path) -> tuple[str, str]:
    guessed_type, _ = mimetypes.guess_type(path.name)
    if not guessed_type:
        return ("application", "octet-stream")
    main_type, sub_type = guessed_type.split("/", 1)
    return (main_type, sub_type)


def _prepare_file_attachment(path: Path) -> FileAttachment:
    if path.suffix.lower() in {".md", ".markdown"}:
        html = wrap_html(path.stem, markdown_to_html(path.read_text(encoding="utf-8")))
        return FileAttachment(
            filename=f"{path.stem}.html",
            attachment_bytes=html.encode("utf-8"),
            mime_type=("text", "html"),
            extension="html",
        )
    return FileAttachment(
        filename=path.name,
        attachment_bytes=path.read_bytes(),
        mime_type=_guess_mime_type(path),
        extension=path.suffix.lstrip(".") or "bin",
    )


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    blockquote: list[str] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_markdown_inline_to_html(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    def flush_blockquote() -> None:
        if blockquote:
            blocks.append(f"<blockquote>{_markdown_inline_to_html(' '.join(blockquote))}</blockquote>")
            blockquote.clear()

    def flush_code_block() -> None:
        if code_lines:
            escaped_code = html_escape("\n".join(code_lines))
            blocks.append(f"<pre><code>{escaped_code}</code></pre>")
            code_lines.clear()

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                flush_code_block()
                in_code_block = False
            else:
                flush_paragraph()
                flush_list()
                flush_blockquote()
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            flush_blockquote()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_markdown_inline_to_html(heading.group(2))}</h{level}>")
            continue

        item = re.match(r"^(?:[-*+]|\d+\.)\s+(.+)$", stripped)
        if item:
            flush_paragraph()
            flush_blockquote()
            list_items.append(_markdown_inline_to_html(item.group(1)))
            continue

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            flush_paragraph()
            flush_list()
            blockquote.append(quote.group(1))
            continue

        flush_list()
        flush_blockquote()
        paragraph.append(stripped)

    flush_code_block()
    flush_paragraph()
    flush_list()
    flush_blockquote()
    return "\n".join(blocks)


def _markdown_inline_to_html(text: str) -> str:
    code_spans: list[str] = []

    def store_code_span(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{html_escape(match.group(1))}</code>")
        return f"\0CODE{len(code_spans) - 1}\0"

    protected = re.sub(r"`([^`]+)`", store_code_span, text)
    escaped = html_escape(protected)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1"/>', escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    for index, code_span in enumerate(code_spans):
        escaped = escaped.replace(f"\0CODE{index}\0", code_span)
    return escaped


def load_delivery_service(extractor) -> KindleDeliveryService:
    return KindleDeliveryService(
        extractor=extractor,
        smtp_sender=SmtpSender(
            kindle_email=os.getenv("KINDLE_EMAIL", ""),
            smtp_server=os.getenv("SMTP_SERVER"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
        ),
    )
