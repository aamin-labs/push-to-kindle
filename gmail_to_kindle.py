"""Poll a Gmail label and send matching emails to Kindle as HTML attachments."""

from __future__ import annotations

import email
import imaplib
import os
import re
import ssl
import tempfile
from dataclasses import dataclass
from email.message import Message
from html import escape as html_escape
from pathlib import Path
from typing import Protocol

from app_helpers import JsonDictStore
from kindle_delivery import DeliveryResult, SmtpSender, wrap_html
from article_pipeline import safe_filename


@dataclass
class InboxEmail:
    uid: str
    subject: str
    sender: str
    date: str
    message_id: str
    html_body: str


class ImapClient(Protocol):
    def login(self, user: str, password: str): ...
    def select(self, mailbox: str): ...
    def uid(self, command: str, *args): ...
    def logout(self): ...


class GmailLabelPoller:
    def __init__(
        self,
        *,
        imap_server: str,
        imap_user: str,
        imap_password: str,
        label: str = "Kindle",
        processed_label: str = "Kindle/Sent",
        smtp_sender: SmtpSender,
        state_path: Path | None = None,
        json_store: JsonDictStore | None = None,
        imap_factory=None,
    ):
        self.imap_server = imap_server
        self.imap_user = imap_user
        self.imap_password = imap_password
        self.label = label
        self.processed_label = processed_label
        self.smtp_sender = smtp_sender
        self.state_path = state_path or (Path.home() / "logs" / "kindle-email-processed.json")
        self.json_store = json_store or JsonDictStore()
        self.imap_factory = imap_factory or self._default_imap_factory

    @staticmethod
    def _default_imap_factory(server: str) -> ImapClient:
        return imaplib.IMAP4_SSL(server, ssl_context=ssl.create_default_context())

    def poll(self, *, limit: int = 10, dry_run: bool = False) -> list[DeliveryResult]:
        if not (self.imap_server and self.imap_user and self.imap_password):
            raise RuntimeError("IMAP_SERVER, IMAP_USER, and IMAP_PASSWORD must be set in .env.")

        processed = self.json_store.load(self.state_path)
        imap = self.imap_factory(self.imap_server)
        results: list[DeliveryResult] = []
        try:
            imap.login(self.imap_user, self.imap_password)
            self._expect_ok(imap.select(_quote_mailbox(self.label)), f"select label {self.label!r}")
            status, data = imap.uid("SEARCH", None, "ALL")
            self._expect_ok((status, data), "search messages")
            uids = (data[0] or b"").decode().split()

            for uid in uids:
                if len(results) >= limit:
                    break
                status, payload = imap.uid("FETCH", uid, "(RFC822)")
                self._expect_ok((status, payload), f"fetch message {uid}")
                raw = _first_rfc822_payload(payload)
                if not raw:
                    continue
                inbox_email = parse_email(uid, raw)
                key = inbox_email.message_id or inbox_email.uid
                if processed.get(key):
                    continue

                if dry_run:
                    out_path = Path(tempfile.gettempdir()) / f"{safe_filename(inbox_email.subject)}.html"
                    out_path.write_text(inbox_email.html_body, encoding="utf-8")
                    results.append(DeliveryResult(inbox_email.subject, "dry-run", str(out_path)))
                else:
                    filename = f"{safe_filename(inbox_email.subject)}.html"
                    self.smtp_sender.send_attachment(
                        inbox_email.subject,
                        inbox_email.html_body.encode("utf-8"),
                        ("text", "html"),
                        "html",
                        filename=filename,
                    )
                    self._mark_processed(imap, uid)
                    processed[key] = inbox_email.subject
                    self.json_store.save(self.state_path, processed)
                    results.append(DeliveryResult(inbox_email.subject, "email"))
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return results

    def _mark_processed(self, imap: ImapClient, uid: str) -> None:
        # Gmail IMAP supports X-GM-LABELS. If it fails, local state still prevents duplicates.
        imap.uid("STORE", uid, "+X-GM-LABELS", f'("{self.processed_label}")')
        imap.uid("STORE", uid, "-X-GM-LABELS", f'("{self.label}")')

    @staticmethod
    def _expect_ok(response, action: str) -> None:
        status, data = response
        if status != "OK":
            raise RuntimeError(f"Could not {action}: {data}")


def parse_email(uid: str, raw_bytes: bytes) -> InboxEmail:
    msg = email.message_from_bytes(raw_bytes)
    subject = _decode_header(msg.get("Subject")) or "Email"
    sender = _decode_header(msg.get("From"))
    date = _decode_header(msg.get("Date"))
    message_id = (msg.get("Message-ID") or "").strip()
    body = _extract_body(msg)
    html_body = _render_email_html(subject=subject, sender=sender, date=date, body=body)
    return InboxEmail(uid, subject, sender, date, message_id, html_body)


def _extract_body(msg: Message) -> str:
    html_part = None
    text_part = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            content_type = part.get_content_type()
            decoded = _decode_part(part)
            if content_type == "text/html" and decoded and html_part is None:
                html_part = _clean_email_html(decoded)
            elif content_type == "text/plain" and decoded and text_part is None:
                text_part = _plain_text_to_html(decoded)
    else:
        decoded = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            html_part = _clean_email_html(decoded)
        else:
            text_part = _plain_text_to_html(decoded)
    return html_part or text_part or "<p>(No readable email body found.)</p>"


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        payload = part.get_payload()
        if isinstance(payload, str):
            return payload
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _clean_email_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<style\b[^>]*>.*?</style>", "", value, flags=re.IGNORECASE | re.DOTALL)
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", value, flags=re.IGNORECASE | re.DOTALL)
    value = body_match.group(1) if body_match else value
    value = re.sub(r"\s(on\w+)=([\"']).*?\2", "", value, flags=re.IGNORECASE | re.DOTALL)
    return value.strip()


def _plain_text_to_html(value: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", value) if p.strip()]
    if not paragraphs:
        return ""
    return "\n".join(f"<p>{html_escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs)


def _render_email_html(*, subject: str, sender: str, date: str, body: str) -> str:
    meta = "".join(
        f"<p><strong>{label}:</strong> {html_escape(value)}</p>"
        for label, value in (("From", sender), ("Date", date))
        if value
    )
    header = f"<section>{meta}</section><hr/>" if meta else ""
    return wrap_html(subject, header + body)


def _first_rfc822_payload(payload) -> bytes | None:
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace('"', '\\"')
    return f'"{escaped}"'


def load_gmail_poller() -> GmailLabelPoller:
    return GmailLabelPoller(
        imap_server=os.getenv("IMAP_SERVER", "imap.gmail.com"),
        imap_user=os.getenv("IMAP_USER", os.getenv("SMTP_USER", "")),
        imap_password=os.getenv("IMAP_PASSWORD", os.getenv("SMTP_PASSWORD", "")),
        label=os.getenv("KINDLE_GMAIL_LABEL", "Kindle"),
        processed_label=os.getenv("KINDLE_PROCESSED_LABEL", "Kindle/Sent"),
        smtp_sender=SmtpSender(
            kindle_email=os.getenv("KINDLE_EMAIL", ""),
            smtp_server=os.getenv("SMTP_SERVER"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
        ),
    )
