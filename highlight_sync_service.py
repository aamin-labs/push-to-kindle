"""Workflow for reconciling Kindle highlights into Bear notes."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from app_helpers import BearClient, JsonDictStore


_RE_LOCATION = re.compile(r"Location ([\d\-]+)")
_RE_DATE = re.compile(r"Added on \w+, (.+)$")


@dataclass
class Highlight:
    title: str
    location: str
    date_str: str
    text: str
    hash: str


@dataclass
class SyncResult:
    total_new: int = 0
    processed_titles: int = 0
    created_notes: int = 0
    updated_notes: int = 0
    unmatched_highlights: int = 0
    messages: list[str] = field(default_factory=list)


def parse_clippings(path: Path) -> dict[str, list[Highlight]]:
    raw = path.read_text(encoding="utf-8-sig")
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

        highlight = Highlight(
            title=title,
            location=location,
            date_str=date_str,
            text=text,
            hash=hashlib.sha256(f"{title}|{location}|{text}".encode()).hexdigest()[:16],
        )
        result.setdefault(title, []).append(highlight)
    return result


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", unescape(title).lower().strip())


def match_title(kindle_title: str, bear_map: dict[str, str]) -> str | None:
    if not bear_map:
        return None

    norm_kindle = _normalize_title(kindle_title)
    norm_map = {_normalize_title(key): value for key, value in bear_map.items()}
    if norm_kindle in norm_map:
        return norm_map[norm_kindle]

    matches = difflib.get_close_matches(norm_kindle, norm_map.keys(), n=1, cutoff=0.7)
    if matches:
        return norm_map[matches[0]]
    return None


def _build_norm_map(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char in " \t\n\r":
            if not in_space:
                normalized.append(" ")
                positions.append(index)
                in_space = True
        else:
            normalized.append(char)
            positions.append(index)
            in_space = False
    return "".join(normalized), positions


def apply_highlights(note_body: str, highlights: list[Highlight]) -> tuple[str, list[Highlight]]:
    norm_body, orig_positions = _build_norm_map(note_body)
    found: list[tuple[int, int]] = []
    unmatched: list[Highlight] = []

    for highlight in highlights:
        norm_text = " ".join(highlight.text.split())
        idx = norm_body.find(norm_text)
        if idx == -1:
            unmatched.append(highlight)
            continue

        orig_start = orig_positions[idx]
        orig_end = orig_positions[idx + len(norm_text) - 1] + 1
        found.append((orig_start, orig_end))

    found.sort(key=lambda span: span[0], reverse=True)
    result = note_body
    for start, end in found:
        result = result[:start] + "==" + result[start:end] + "==" + result[end:]

    return result, unmatched


class HighlightSyncService:
    def __init__(
        self,
        bear_client: BearClient | None = None,
        json_store: JsonDictStore | None = None,
        *,
        bear_map_path: Path | None = None,
        seen_log_path: Path | None = None,
    ):
        self._bear_client = bear_client or BearClient()
        self._json_store = json_store or JsonDictStore()
        self._bear_map_path = bear_map_path or (Path.home() / "logs" / "kindle-bear-map.json")
        self._seen_log_path = seen_log_path or (Path.home() / "logs" / "kindle-seen.json")

    def sync(self, clippings_path: Path, *, dry_run: bool = False) -> SyncResult:
        bear_map = self._json_store.load(self._bear_map_path)
        seen = self._json_store.load(self._seen_log_path)
        clippings = parse_clippings(clippings_path)
        result = SyncResult()

        for kindle_title, highlights in clippings.items():
            new = [highlight for highlight in highlights if highlight.hash not in seen]
            if not new:
                continue

            result.total_new += len(new)
            result.processed_titles += 1
            note_id = match_title(kindle_title, bear_map)

            if dry_run:
                label = f"Bear note {note_id[:8]}…" if note_id else "fallback note"
                result.messages.append(f"\n{kindle_title}")
                result.messages.append(f"  → {label}  ({len(new)} new highlight(s))")
                for highlight in new:
                    preview = highlight.text[:80] + ("…" if len(highlight.text) > 80 else "")
                    result.messages.append(f"  [{highlight.location}] {preview}")
                continue

            if note_id:
                note = self._bear_client.open_note(note_id)
                if not note or "note" not in note:
                    result.messages.append(f"Warning: could not fetch Bear note for {kindle_title!r}")
                    continue

                body, unmatched = apply_highlights(note["note"], new)
                if unmatched:
                    body += "\n\n## Unmatched Highlights\n\n"
                    for highlight in unmatched:
                        body += f"=={highlight.text}==\n\n*Location {highlight.location} · {highlight.date_str}*\n\n"
                self._bear_client.replace_note_text(note_id, body)
                result.updated_notes += 1
                result.unmatched_highlights += len(unmatched)
                result.messages.append(
                    f"{kindle_title!r}: {len(new) - len(unmatched)} inlined, {len(unmatched)} unmatched"
                )
            else:
                text = f"Kindle document: {kindle_title}\n\n"
                for highlight in new:
                    text += f"=={highlight.text}==\n\n*Location {highlight.location} · {highlight.date_str}*\n\n"
                self._bear_client.create_note(kindle_title, text, "0a/reading/highlights")
                result.created_notes += 1
                result.messages.append(f"{kindle_title!r}: created fallback note ({len(new)} highlight(s))")

            for highlight in new:
                seen[highlight.hash] = True

        if dry_run:
            result.messages.append(
                f"\nDry run: {result.total_new} new highlight(s) across {result.processed_titles} document(s)."
            )
            return result

        if result.total_new:
            self._json_store.save(self._seen_log_path, seen)
            result.messages.append(
                f"\nDone. {result.total_new} new highlight(s) across {result.processed_titles} document(s)."
            )
        else:
            result.messages.append("No new highlights.")
        return result
