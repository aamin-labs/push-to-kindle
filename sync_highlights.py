#!/usr/bin/env python3
"""Sync Kindle highlights into Bear notes as inline ==highlights==."""

import argparse
from pathlib import Path

from highlight_sync_service import (
    Highlight,
    HighlightSyncService,
    apply_highlights,
    match_title,
    parse_clippings,
)

CLIPPINGS_PATH = Path("/Volumes/Kindle/documents/My Clippings.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Kindle highlights into Bear notes.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without touching Bear.",
    )
    parser.add_argument(
        "--clippings",
        metavar="PATH",
        default=str(CLIPPINGS_PATH),
        help="Path to My Clippings.txt",
    )
    args = parser.parse_args()

    clippings_path = Path(args.clippings)
    if not clippings_path.exists():
        parser.exit(status=1, message=f"Error: Clippings file not found: {clippings_path}\n")

    service = HighlightSyncService()
    result = service.sync(clippings_path, dry_run=args.dry_run)
    for message in result.messages:
        print(message)


if __name__ == "__main__":
    main()
