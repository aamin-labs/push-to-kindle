#!/usr/bin/env python3
"""Send Gmail messages labelled for Kindle as HTML attachments."""

import argparse
from pathlib import Path

from gmail_to_kindle import load_gmail_poller


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path(__file__).parent / ".env")


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Poll a Gmail label and send emails to Kindle.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum labelled emails to process")
    parser.add_argument("--dry-run", action="store_true", help="Write HTML previews instead of sending")
    args = parser.parse_args()

    try:
        results = load_gmail_poller().poll(limit=args.limit, dry_run=args.dry_run)
    except Exception as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")

    if not results:
        print("No Kindle-labelled emails found.")
        return
    for result in results:
        if result.delivered_format == "dry-run":
            print(f"Previewed email: {result.title} -> {result.output_path}")
        else:
            print(f"Sent email to Kindle: {result.title}")


if __name__ == "__main__":
    main()
