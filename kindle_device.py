"""Kindle device access — reads and manages documents at /Volumes/Kindle."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

KINDLE_MOUNT = Path("/Volumes/Kindle")
DOCUMENTS_DIR = KINDLE_MOUNT / "documents" / "Downloads" / "Items01"

ARTICLE_EXTENSIONS = {".kfx", ".azw3", ".mobi", ".epub", ".html", ".pdf"}

# Purchased books end with an ASIN: _B followed by 9 alphanumeric chars
_ASIN_RE = re.compile(r"_B[0-9A-Z]{9}$", re.IGNORECASE)
# Personal documents end with zero or more short lowercase slugs + a long uppercase ID
_PERSONAL_DOC_ID_RE = re.compile(r"(_[a-z0-9]{2,12})*_[A-Z0-9]{24,}$")
# Junk prefixes Amazon or the tool sometimes prepends to the title
_JUNK_PREFIX_RE = re.compile(r"^title\s*[_ ]+", re.IGNORECASE)


def _extract_title(stem: str) -> str | None:
    """Return a human-readable title from a Kindle filename stem.

    Returns None if the file is a purchased book or sample (should be excluded).
    """
    if _ASIN_RE.search(stem):
        return None  # purchased book
    if "_sample" in stem.lower():
        return None  # book sample

    # Strip personal document ID suffixes to recover the title
    title = _PERSONAL_DOC_ID_RE.sub("", stem)
    title = _JUNK_PREFIX_RE.sub("", title)
    title = title.strip("_ ")
    return title or None


SNIPPETS_PATH = Path.home() / "logs" / "kindle-snippets.json"


def _load_snippets() -> dict[str, str]:
    try:
        data = json.loads(SNIPPETS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _match_snippet(title: str, snippets: dict[str, str]) -> str | None:
    """Find a snippet for a title, tolerating Amazon's filename truncation."""
    if title in snippets:
        return snippets[title]
    # Long titles get truncated in the Kindle filename — check if any key starts with the title
    for key, snippet in snippets.items():
        if key.startswith(title) or title.startswith(key):
            return snippet
    return None


@dataclass
class Document:
    title: str
    filename: str
    snippet: str | None = None


def is_connected() -> bool:
    """Return True if the Kindle is mounted at /Volumes/Kindle."""
    return KINDLE_MOUNT.exists()


def list_documents() -> list[Document]:
    """Return all personal document articles on the Kindle, excluding books."""
    if not DOCUMENTS_DIR.exists():
        return []

    snippets = _load_snippets()
    docs = []
    for path in sorted(DOCUMENTS_DIR.iterdir()):
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in ARTICLE_EXTENSIONS:
            continue

        title = _extract_title(path.stem)
        if title is None:
            continue  # purchased book or sample

        snippet = _match_snippet(title, snippets)
        docs.append(Document(title=title, filename=path.name, snippet=snippet))

    return docs
