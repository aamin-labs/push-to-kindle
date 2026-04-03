#!/usr/bin/env python3
"""Sync Kindle highlights into Bear notes as inline ==highlights==."""

import sys
import json
import hashlib
import difflib
import subprocess
import threading
import re
import argparse
from dataclasses import dataclass
from html import unescape
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote

CLIPPINGS_PATH = Path("/Volumes/Kindle/documents/My Clippings.txt")
BEAR_MAP_PATH = Path.home() / "logs" / "kindle-bear-map.json"
SEEN_LOG_PATH = Path.home() / "logs" / "kindle-seen.json"

_RE_LOCATION = re.compile(r"Location ([\d\-]+)")
_RE_DATE = re.compile(r"Added on \w+, (.+)$")


@dataclass
class Highlight:
    title: str
    location: str
    date_str: str
    text: str
    hash: str


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Clippings parser ──────────────────────────────────────────────────────────

def parse_clippings(path: Path) -> dict[str, list[Highlight]]:
    """Parse My Clippings.txt → {kindle_title: [Highlight, ...]}"""
    raw = path.read_text(encoding="utf-8-sig")  # strips file-level BOM
    result: dict[str, list[Highlight]] = {}

    for entry in raw.split("\n==========\n"):
        lines = entry.strip().splitlines()
        if len(lines) < 2:
            continue

        title = lines[0].lstrip("\ufeff").strip()
        meta = lines[1].strip()

        if "- Your Highlight" not in meta:
            continue

        loc_match = _RE_LOCATION.search(meta)
        date_match = _RE_DATE.search(meta)
        location = loc_match.group(1) if loc_match else ""
        date_str = date_match.group(1).strip() if date_match else ""

        text = "\n".join(lines[3:]).strip() if len(lines) > 3 else ""
        if not text or "<You have reached the clipping limit" in text:
            continue

        h = Highlight(
            title=title,
            location=location,
            date_str=date_str,
            text=text,
            hash=hashlib.sha256(f"{title}|{location}|{text}".encode()).hexdigest()[:16],
        )
        result.setdefault(title, []).append(h)

    return result


# ── Title matching ────────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", unescape(title).lower().strip())


def match_title(kindle_title: str, bear_map: dict[str, str]) -> str | None:
    """Return Bear note ID for a Kindle title. Exact match first, fuzzy fallback."""
    if not bear_map:
        return None

    norm_kindle = _normalize_title(kindle_title)
    norm_map = {_normalize_title(k): v for k, v in bear_map.items()}

    if norm_kindle in norm_map:
        return norm_map[norm_kindle]

    matches = difflib.get_close_matches(norm_kindle, norm_map.keys(), n=1, cutoff=0.7)
    if matches:
        return norm_map[matches[0]]

    return None


# ── Bear x-callback-url helper ────────────────────────────────────────────────

def _bear_callback_html() -> str:
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
    callback_html = _bear_callback_html()

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


# ── Inline highlighting ───────────────────────────────────────────────────────

def _build_norm_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace in text, returning (normalized, orig_positions).

    orig_positions[i] gives the index in the original text for normalized char i.
    """
    normalized: list[str] = []
    positions: list[int] = []
    in_space = False

    for i, ch in enumerate(text):
        if ch in " \t\n\r":
            if not in_space:
                normalized.append(" ")
                positions.append(i)
                in_space = True
        else:
            normalized.append(ch)
            positions.append(i)
            in_space = False

    return "".join(normalized), positions


def apply_highlights(note_body: str, highlights: list[Highlight]) -> tuple[str, list[Highlight]]:
    """Wrap found passages with ==...==. Returns (modified_body, unmatched).

    Applies back-to-front so earlier offsets are not shifted by insertions.
    """
    norm_body, orig_positions = _build_norm_map(note_body)
    found: list[tuple[int, int]] = []
    unmatched: list[Highlight] = []

    for h in highlights:
        norm_text = " ".join(h.text.split())
        idx = norm_body.find(norm_text)
        if idx == -1:
            unmatched.append(h)
            continue

        orig_start = orig_positions[idx]
        orig_end = orig_positions[idx + len(norm_text) - 1] + 1
        found.append((orig_start, orig_end))

    found.sort(key=lambda s: s[0], reverse=True)
    result = note_body
    for start, end in found:
        result = result[:start] + "==" + result[start:end] + "==" + result[end:]

    return result, unmatched


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Kindle highlights into Bear notes.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without touching Bear.")
    parser.add_argument("--clippings", metavar="PATH", default=str(CLIPPINGS_PATH),
                        help="Path to My Clippings.txt")
    args = parser.parse_args()

    clippings_path = Path(args.clippings)
    if not clippings_path.exists():
        sys.exit(f"Error: Clippings file not found: {clippings_path}")

    bear_map = load_json(BEAR_MAP_PATH)
    seen = load_json(SEEN_LOG_PATH)
    clippings = parse_clippings(clippings_path)

    total_new = 0
    processed_titles = 0

    for kindle_title, highlights in clippings.items():
        new = [h for h in highlights if h.hash not in seen]
        if not new:
            continue

        total_new += len(new)
        processed_titles += 1
        note_id = match_title(kindle_title, bear_map)

        if args.dry_run:
            label = f"Bear note {note_id[:8]}…" if note_id else "fallback note"
            print(f"\n{kindle_title}")
            print(f"  → {label}  ({len(new)} new highlight(s))")
            for h in new:
                preview = h.text[:80] + ("…" if len(h.text) > 80 else "")
                print(f"  [{h.location}] {preview}")
            continue

        if note_id:
            result = bear_call(
                f"bear://x-callback-url/open-note?id={quote(note_id)}&show_window=no"
            )
            if not result or "note" not in result:
                print(f"Warning: could not fetch Bear note for {kindle_title!r}", file=sys.stderr)
                continue

            body, unmatched = apply_highlights(result["note"], new)

            if unmatched:
                body += "\n\n## Unmatched Highlights\n\n"
                for h in unmatched:
                    body += f"=={h.text}==\n\n*Location {h.location} · {h.date_str}*\n\n"

            subprocess.run(
                ["open", f"bear://x-callback-url/add-text?id={quote(note_id)}"
                         f"&text={quote(body)}&mode=replace_all"],
                check=True,
            )
            n_inlined = len(new) - len(unmatched)
            print(f"{kindle_title!r}: {n_inlined} inlined, {len(unmatched)} unmatched")

        else:
            text = f"Kindle document: {kindle_title}\n\n"
            for h in new:
                text += f"=={h.text}==\n\n*Location {h.location} · {h.date_str}*\n\n"
            subprocess.run(
                ["open", f"bear://x-callback-url/create"
                         f"?title={quote(kindle_title)}"
                         f"&text={quote(text)}"
                         f"&tags={quote('0a/reading/highlights')}"],
                check=True,
            )
            print(f"{kindle_title!r}: created fallback note ({len(new)} highlight(s))")

        for h in new:
            seen[h.hash] = True

    if args.dry_run:
        print(f"\nDry run: {total_new} new highlight(s) across {processed_titles} document(s).")
        return

    if total_new:
        save_json(SEEN_LOG_PATH, seen)
        print(f"\nDone. {total_new} new highlight(s) across {processed_titles} document(s).")
    else:
        print("No new highlights.")


if __name__ == "__main__":
    main()
