import os
import sys
import types
import unittest

os.environ.setdefault("KINDLE_EMAIL", "test@kindle.com")

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *args, **kwargs: None
requests = types.ModuleType("requests")
trafilatura = types.ModuleType("trafilatura")
lxml = types.ModuleType("lxml")
lxml.etree = types.ModuleType("etree")
lxml.html = types.ModuleType("html")

sys.modules.setdefault("dotenv", dotenv)
sys.modules.setdefault("requests", requests)
sys.modules.setdefault("trafilatura", trafilatura)
sys.modules.setdefault("lxml", lxml)
sys.modules.setdefault("lxml.etree", lxml.etree)
sys.modules.setdefault("lxml.html", lxml.html)

import send_to_kindle


class BearCallbackHtmlTests(unittest.TestCase):
    def test_callback_page_tries_to_close_tab(self):
        html = send_to_kindle._bear_callback_html()
        self.assertIn("window.close()", html)
        self.assertIn('location.replace("about:blank")', html)
        self.assertIn("Bear callback complete.", html)


if __name__ == "__main__":
    unittest.main()
