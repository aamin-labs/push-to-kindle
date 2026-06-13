import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from app_helpers import JsonDictStore
from gmail_to_kindle import GmailLabelPoller, parse_email


class FakeSmtpSender:
    def __init__(self):
        self.sent = []

    def send_attachment(self, title, attachment_bytes, mime_type, extension, *, filename=None):
        self.sent.append((title, attachment_bytes, mime_type, extension, filename))


class FakeImap:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user, password))
        return "OK", []

    def select(self, mailbox):
        self.calls.append(("select", mailbox))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(uid.encode() for uid in self.messages)]
        if command == "FETCH":
            uid = args[0]
            return "OK", [(b"1 (RFC822 {1}", self.messages[uid]), b")"]
        if command == "STORE":
            return "OK", []
        raise AssertionError(f"unexpected uid command: {command}")

    def logout(self):
        self.calls.append(("logout",))
        return "OK", []


def _raw_email(*, subject="Long read", html="<html><body><p>Hello</p><script>bad()</script></body></html>"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "Writer <writer@example.com>"
    msg["Date"] = "Sat, 13 Jun 2026 10:00:00 +0000"
    msg["Message-ID"] = "<msg-1@example.com>"
    msg.set_content("Plain fallback")
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


class GmailToKindleTests(unittest.TestCase):
    def test_parse_email_prefers_clean_html_body_and_wraps_metadata(self):
        inbox_email = parse_email("101", _raw_email())

        self.assertEqual("Long read", inbox_email.subject)
        self.assertIn("Writer &lt;writer@example.com&gt;", inbox_email.html_body)
        self.assertIn("<p>Hello</p>", inbox_email.html_body)
        self.assertNotIn("<script>", inbox_email.html_body)
        self.assertIn("<title>Long read</title>", inbox_email.html_body)

    def test_poll_sends_labelled_email_as_html_attachment_and_marks_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_imap = FakeImap({"101": _raw_email()})
            smtp = FakeSmtpSender()
            poller = GmailLabelPoller(
                imap_server="imap.gmail.com",
                imap_user="me@gmail.com",
                imap_password="secret",
                label="Kindle",
                processed_label="Kindle/Sent",
                smtp_sender=smtp,
                state_path=Path(tmp) / "processed.json",
                json_store=JsonDictStore(),
                imap_factory=lambda server: fake_imap,
            )

            results = poller.poll()

            self.assertEqual(1, len(results))
            self.assertEqual("email", results[0].delivered_format)
            self.assertEqual(1, len(smtp.sent))
            title, payload, mime_type, extension, filename = smtp.sent[0]
            self.assertEqual("Long read", title)
            self.assertEqual(("text", "html"), mime_type)
            self.assertEqual("html", extension)
            self.assertEqual("Long read.html", filename)
            self.assertIn(b"<p>Hello</p>", payload)
            self.assertIn(("uid", "STORE", "101", "+X-GM-LABELS", '("Kindle/Sent")'), fake_imap.calls)
            self.assertIn(("uid", "STORE", "101", "-X-GM-LABELS", '("Kindle")'), fake_imap.calls)

            # Local state is the duplicate guard if Gmail label mutation is flaky.
            again = poller.poll()
            self.assertEqual([], again)
            self.assertEqual(1, len(smtp.sent))

    def test_poll_dry_run_writes_preview_without_sending_or_marking_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_imap = FakeImap({"101": _raw_email(subject="Preview")})
            smtp = FakeSmtpSender()
            poller = GmailLabelPoller(
                imap_server="imap.gmail.com",
                imap_user="me@gmail.com",
                imap_password="secret",
                smtp_sender=smtp,
                state_path=Path(tmp) / "processed.json",
                imap_factory=lambda server: fake_imap,
            )
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                results = poller.poll(dry_run=True)
            finally:
                os.chdir(cwd)

            self.assertEqual("dry-run", results[0].delivered_format)
            self.assertTrue(Path(results[0].output_path).exists())
            self.assertEqual([], smtp.sent)
            self.assertNotIn(("uid", "STORE", "101", "+X-GM-LABELS", '("Kindle/Sent")'), fake_imap.calls)


if __name__ == "__main__":
    unittest.main()
