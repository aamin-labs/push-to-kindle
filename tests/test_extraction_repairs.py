import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("KINDLE_EMAIL", "test@kindle.com")

for module_name in ("article_pipeline", "lxml", "lxml.etree", "lxml.html", "trafilatura", "requests", "dotenv"):
    sys.modules.pop(module_name, None)

try:
    import article_pipeline
except ModuleNotFoundError:
    article_pipeline = None

try:
    from lxml import html as _lxml_html  # noqa: F401
except ModuleNotFoundError:
    _lxml_html = None


@unittest.skipIf(article_pipeline is None, "runtime deps not installed")
class ExtractionRepairTests(unittest.TestCase):
    def test_fetch_raw_html_falls_back_after_trafilatura_403(self):
        trafilatura_error = RuntimeError("403 Client Error: Forbidden for url: https://example.com/article")
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.text = "<html><body>ok</body></html>"

        with mock.patch.object(article_pipeline, "_trafilatura_module") as trafilatura_module, mock.patch.object(
            article_pipeline, "_requests_module"
        ) as requests_module:
            trafilatura_module.return_value.fetch_url.side_effect = trafilatura_error
            requests_module.return_value.get.return_value = fake_response

            html = article_pipeline._fetch_raw_html("https://example.com/article")

        self.assertEqual("<html><body>ok</body></html>", html)
        requests_module.return_value.get.assert_called_once_with(
            "https://example.com/article",
            timeout=15,
            headers=article_pipeline._BROWSER_HEADERS,
        )

    @unittest.skipIf(_lxml_html is None, "lxml not installed")
    def test_raw_preserved_content_keeps_lists_and_images(self):
        filler = (
            "This article section contains enough supporting detail to look like a real blog post body, "
            "with multiple clauses, explanatory context, and repeated phrasing that pushes the sample well "
            "past the minimum size threshold used by the extractor. "
        ) * 3
        raw_html = """
        <html>
          <body>
            <div class="w-richtext">
              <p>Intro paragraph that is long enough to count as article content.</p>
              <p>""" + filler + """</p>
              <p>This distinction matters because these two types of skills may need testing for different reasons:</p>
              <ul>
                <li>Capability uplift skills may become less necessary as models improve.</li>
                <li>Encoded preference skills are more durable.</li>
              </ul>
              <figure><div><img src="https://example.com/image.png" alt="Diagram"></div></figure>
            </div>
          </body>
        </html>
        """
        html, markdown = article_pipeline.extract_raw_preserved_content(
            raw_html, "https://example.com/article"
        )

        self.assertIn("<ul>", html)
        self.assertIn("<li>Capability uplift skills may become less necessary as models improve.</li>", html)
        self.assertIn('<img src="https://example.com/image.png" alt="Diagram"/>', html)
        self.assertIn("- Capability uplift skills may become less necessary as models improve.", markdown)
        self.assertIn("![Diagram](https://example.com/image.png)", markdown)

    def test_prefers_raw_when_extractor_drops_structure(self):
        filler = (
            "Capability uplift and encoded preference skills both matter for article extraction, and the "
            "test fixture needs enough surrounding prose to resemble a real article rather than a toy fragment. "
        ) * 4
        extracted_html = (
            "<p>This distinction matters because these two types of skills may need testing for different reasons:</p>"
            f"<p>{filler}</p>"
            "<p>Capability uplift and encoded preference skills both matter for article extraction, but this version lost the list entirely and flattened everything into plain paragraphs.</p>"
            "<p>The result looks readable at a glance, but it silently drops structure that should survive in both Kindle HTML and Bear markdown output.</p>"
            "<p>Either way, testing turns a skill that seems to work into one you know works.</p>"
        )
        raw_html = (
            "<p>This distinction matters because these two types of skills may need testing for different reasons:</p>"
            f"<p>{filler}</p>"
            "<p>Capability uplift and encoded preference skills both matter for article extraction, but this version preserved the original structure and supporting media.</p>"
            "<ul><li>Capability uplift skills may become less necessary as models improve.</li></ul>"
            '<figure><img src="https://example.com/image.png" alt=""/></figure>'
            "<p>The result keeps structure that should survive in both Kindle HTML and Bear markdown output.</p>"
            "<p>Either way, testing turns a skill that seems to work into one you know works.</p>"
        )

        self.assertTrue(article_pipeline.should_prefer_raw_content(extracted_html, raw_html))
        self.assertTrue(
            article_pipeline.should_prefer_raw_markdown(
                f"This distinction matters.\n\n{filler}\n\nEither way, testing turns a skill that seems to work into one you know works.\n",
                f"This distinction matters.\n\n{filler}\n\n- Capability uplift skills may become less necessary as models improve.\n\n![Diagram](https://example.com/image.png)\n",
            )
        )

    def test_raw_fallback_embeds_images_when_including_images(self):
        filler = (
            "Managed agents need durable article extraction behavior, including images that remain available "
            "after the document is delivered to a Kindle device. "
        ) * 8
        extracted_html = f"<p>{filler}</p>"
        raw_html_content = (
            f"<p>{filler}</p>"
            '<figure><img src="https://example.com/image.png" alt="Diagram"/></figure>'
        )

        fake_trafilatura = mock.Mock()
        fake_trafilatura.bare_extraction.return_value = mock.Mock(title="Article")

        def extract(*args, **kwargs):
            output_format = kwargs["output_format"]
            if output_format == "xml":
                return ""
            if output_format == "html":
                return extracted_html
            if output_format == "markdown":
                return filler
            return ""

        fake_trafilatura.extract.side_effect = extract
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.headers = {"Content-Type": "image/png"}
        fake_response.content = b"image-bytes"

        with mock.patch.object(article_pipeline, "_fetch_raw_html", return_value="<html></html>"), mock.patch.object(
            article_pipeline, "_trafilatura_module", return_value=fake_trafilatura
        ), mock.patch.object(
            article_pipeline, "extract_raw_preserved_content", return_value=(raw_html_content, filler)
        ), mock.patch.object(article_pipeline, "_requests_module") as requests_module:
            requests_module.return_value.get.return_value = fake_response

            article = article_pipeline.ArticleExtractor().extract_url(
                "https://example.com/article", include_images=True
            )

        self.assertIn('src="data:image/png;base64,', article.html_content)
        self.assertNotIn('src="https://example.com/image.png"', article.html_content)


if __name__ == "__main__":
    unittest.main()
