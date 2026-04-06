import os
import sys
import unittest

os.environ.setdefault("KINDLE_EMAIL", "test@kindle.com")

for module_name in ("send_to_kindle", "lxml", "lxml.etree", "lxml.html", "trafilatura", "requests", "dotenv"):
    sys.modules.pop(module_name, None)

try:
    import send_to_kindle
except ModuleNotFoundError:
    send_to_kindle = None


@unittest.skipIf(send_to_kindle is None, "runtime deps not installed")
class ExtractionRepairTests(unittest.TestCase):
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
        html, markdown = send_to_kindle._extract_raw_preserved_content(
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

        self.assertTrue(send_to_kindle._should_prefer_raw_content(extracted_html, raw_html))
        self.assertTrue(
            send_to_kindle._should_prefer_raw_markdown(
                f"This distinction matters.\n\n{filler}\n\nEither way, testing turns a skill that seems to work into one you know works.\n",
                f"This distinction matters.\n\n{filler}\n\n- Capability uplift skills may become less necessary as models improve.\n\n![Diagram](https://example.com/image.png)\n",
            )
        )


if __name__ == "__main__":
    unittest.main()
