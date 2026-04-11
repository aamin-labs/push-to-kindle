import os
import tempfile
import unittest
from pathlib import Path

from article_pipeline import ExtractedArticle
from app_helpers import JsonDictStore
from kindle_delivery import ArticleMetadataStore, KindleDeliveryService


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def prepare_for_kindle(self, url: str, *, include_images: bool = True) -> ExtractedArticle:
        self.calls.append(("prepare_for_kindle", url, include_images))
        if url.startswith("https://x.com/"):
            return ExtractedArticle(
                title="Thread",
                source_url=url,
                markdown_content="# Thread",
                delivery_format="epub",
            )
        return ExtractedArticle(
            title="Example Article",
            source_url=url,
            html_content="<p>Hello world</p>",
            markdown_content="Hello world",
            delivery_format="html",
        )

    def prepare_local_html(self, path: str, *, title_override: str | None = None) -> ExtractedArticle:
        self.calls.append(("prepare_local_html", path, title_override))
        return ExtractedArticle(
            title=title_override or "Local HTML",
            source_url="",
            html_content="<p>Local</p>",
            delivery_format="html",
        )


class FakeSmtpSender:
    def __init__(self):
        self.sent = []

    def send_attachment(self, title, attachment_bytes, mime_type, extension):
        self.sent.append((title, attachment_bytes, mime_type, extension))


class FakeBearClient:
    def __init__(self):
        self.created = []

    def create_note(self, title: str, text: str, tags: str):
        self.created.append((title, text, tags))
        return {"identifier": "note-123"}


class KindleDeliveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir.name)
        self.addCleanup(lambda: os.chdir(self.cwd))

        self.extractor = FakeExtractor()
        self.smtp = FakeSmtpSender()
        self.bear = FakeBearClient()
        self.metadata_store = ArticleMetadataStore(
            json_store=JsonDictStore(),
            snippets_path=Path(self.tmpdir.name) / "snippets.json",
            bear_map_path=Path(self.tmpdir.name) / "bear-map.json",
        )
        self.service = KindleDeliveryService(
            extractor=self.extractor,
            smtp_sender=self.smtp,
            metadata_store=self.metadata_store,
            bear_client=self.bear,
            platform="darwin",
            epub_converter=lambda title, markdown: b"epub-bytes",
        )

    def test_deliver_url_updates_metadata_and_bear_note(self):
        result = self.service.deliver_url("https://example.com/article", include_images=False, dry_run=False)

        self.assertEqual("html", result.delivered_format)
        self.assertEqual("note-123", result.bear_note_id)
        self.assertEqual([("prepare_for_kindle", "https://example.com/article", False)], self.extractor.calls)
        self.assertEqual(1, len(self.smtp.sent))
        self.assertEqual("Example Article", self.smtp.sent[0][0])
        self.assertEqual(("text", "html"), self.smtp.sent[0][2])
        self.assertEqual(1, len(self.bear.created))

    def test_x_url_uses_defuddle_path(self):
        result = self.service.deliver_url("https://x.com/example/status/1", dry_run=False)

        self.assertEqual("epub", result.delivered_format)
        self.assertEqual([("prepare_for_kindle", "https://x.com/example/status/1", True)], self.extractor.calls)
        self.assertEqual(("application", "epub+zip"), self.smtp.sent[0][2])

    def test_dry_run_writes_preview_without_side_effects(self):
        result = self.service.deliver_html_file("article.html", title_override="Preview", dry_run=True)

        self.assertEqual("dry-run", result.delivered_format)
        self.assertTrue(result.output_path.endswith("Preview.html"))
        self.assertTrue(Path(result.output_path).exists())
        self.assertEqual([], self.smtp.sent)
        self.assertEqual([], self.bear.created)


if __name__ == "__main__":
    unittest.main()
