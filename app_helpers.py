"""Shared helpers for local JSON state and Bear x-callback-url flows."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_json_dict(path: Path, key: str, value: str) -> None:
    data = load_json(path)
    data[key] = value
    save_json(path, data)


def bear_callback_html() -> str:
    """Minimal callback page that tries to close itself after Bear redirects to localhost."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bear callback</title>
  <script>
    window.open("", "_self");
    window.close();
    setTimeout(function () {
      document.body.textContent = "Bear callback complete. You can close this tab.";
      location.replace("about:blank");
    }, 80);
  </script>
</head>
<body></body>
</html>
"""


def bear_call(url: str, timeout: int = 8) -> dict | None:
    """Open a Bear x-callback-url and return x-success callback params as a dict."""
    result: dict = {}
    callback_html = bear_callback_html()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            result.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(callback_html.encode("utf-8"))
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("localhost", 0), _Handler)
        port = server.server_address[1]

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        subprocess.run(
            ["open", f"{url}&x-success={quote(f'http://localhost:{port}/')}"],
            check=True,
        )
        server_thread.join(timeout=timeout)
        server.shutdown()
        server.server_close()
        return result if result else None
    except Exception as e:
        print(f"Warning: Bear call failed: {e}", file=sys.stderr)
        return None
