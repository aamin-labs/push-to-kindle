import tempfile
import unittest
from pathlib import Path

from app_helpers import JsonDictStore
from highlight_sync_service import HighlightSyncService


class FakeBearClient:
    def __init__(self, note_body="Alpha Beta Gamma Delta"):
        self.note_body = note_body
        self.opened = []
        self.replaced = []
        self.created = []

    def open_note(self, note_id: str):
        self.opened.append(note_id)
        return {"note": self.note_body}

    def replace_note_text(self, note_id: str, text: str):
        self.replaced.append((note_id, text))

    def create_note(self, title: str, text: str, tags: str):
        self.created.append((title, text, tags))
        return {"identifier": f"note-{len(self.created)}"}


class HighlightSyncWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.json_store = JsonDictStore()
        self.bear_map_path = Path(self.tmpdir.name) / "bear-map.json"
        self.seen_log_path = Path(self.tmpdir.name) / "seen.json"

    def write_clippings(self, text: str) -> Path:
        path = Path(self.tmpdir.name) / "My Clippings.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_sync_updates_existing_note_and_persists_seen_hashes(self):
        self.json_store.save(self.bear_map_path, {"My Article": "note-1"})
        bear = FakeBearClient(note_body="Intro Alpha Beta Gamma Delta Outro")
        service = HighlightSyncService(
            bear_client=bear,
            json_store=self.json_store,
            bear_map_path=self.bear_map_path,
            seen_log_path=self.seen_log_path,
        )
        clippings_path = self.write_clippings(
            "My Article\n"
            "- Your Highlight on page 1 | Location 42-43 | Added on Tuesday, April 1, 2026 10:00:00 AM\n"
            "\n"
            "Alpha Beta Gamma Delta\n"
            "==========\n"
        )

        result = service.sync(clippings_path, dry_run=False)

        self.assertEqual(1, result.total_new)
        self.assertEqual(1, result.updated_notes)
        self.assertEqual(0, result.created_notes)
        self.assertEqual(["note-1"], bear.opened)
        self.assertEqual(1, len(bear.replaced))
        self.assertIn("==Alpha Beta Gamma Delta==", bear.replaced[0][1])
        self.assertEqual(1, len(self.json_store.load(self.seen_log_path)))

    def test_sync_creates_fallback_note_for_unmatched_title(self):
        bear = FakeBearClient()
        service = HighlightSyncService(
            bear_client=bear,
            json_store=self.json_store,
            bear_map_path=self.bear_map_path,
            seen_log_path=self.seen_log_path,
        )
        clippings_path = self.write_clippings(
            "Unknown Article\n"
            "- Your Highlight on page 1 | Location 9 | Added on Tuesday, April 1, 2026 10:00:00 AM\n"
            "\n"
            "Standalone quote\n"
            "==========\n"
        )

        result = service.sync(clippings_path, dry_run=False)

        self.assertEqual(1, result.created_notes)
        self.assertEqual(0, result.updated_notes)
        self.assertEqual(1, len(bear.created))
        self.assertIn("Standalone quote", bear.created[0][1])

    def test_dry_run_reports_without_mutating(self):
        self.json_store.save(self.bear_map_path, {"My Article": "note-1"})
        bear = FakeBearClient()
        service = HighlightSyncService(
            bear_client=bear,
            json_store=self.json_store,
            bear_map_path=self.bear_map_path,
            seen_log_path=self.seen_log_path,
        )
        clippings_path = self.write_clippings(
            "My Article\n"
            "- Your Highlight on page 1 | Location 42 | Added on Tuesday, April 1, 2026 10:00:00 AM\n"
            "\n"
            "Preview text\n"
            "==========\n"
        )

        result = service.sync(clippings_path, dry_run=True)

        self.assertEqual(1, result.total_new)
        self.assertEqual([], bear.replaced)
        self.assertEqual([], bear.created)
        self.assertEqual({}, self.json_store.load(self.seen_log_path))
        self.assertTrue(any("Dry run:" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
