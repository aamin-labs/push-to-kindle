#!/usr/bin/env python3
"""Send a local file to Kindle via Amazon's Personal Documents Service."""

import argparse
from pathlib import Path

from kindle_delivery import load_delivery_service


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path(__file__).parent / ".env")


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Send a local file to your Kindle.")
    parser.add_argument("path", help="Path to the file to send")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the file without sending",
    )
    args = parser.parse_args()

    service = load_delivery_service(extractor=None)
    try:
        service.deliver_file(args.path, dry_run=args.dry_run)
    except Exception as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")


if __name__ == "__main__":
    main()
