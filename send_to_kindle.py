#!/usr/bin/env python3
"""Send a web article to Kindle via Amazon's Personal Documents Service."""

import argparse
from pathlib import Path

from app_helpers import bear_callback_html
from article_pipeline import ArticleExtractor
from kindle_delivery import load_delivery_service


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(Path(__file__).parent / ".env")


def _bear_callback_html() -> str:
    return bear_callback_html()


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Send a web article to your Kindle.")
    parser.add_argument("url", nargs="?", help="URL of the article to send")
    parser.add_argument(
        "--html-file",
        metavar="PATH",
        help="Send a pre-rendered HTML fragment file instead of fetching a URL",
    )
    parser.add_argument(
        "--title",
        metavar="TITLE",
        help="Override the article title (useful with --html-file)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and save HTML locally without sending",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip downloading and embedding images",
    )
    parser.add_argument(
        "--save-to-bear",
        action="store_true",
        help="Create a Bear note too (macOS only)",
    )
    args = parser.parse_args()

    if not args.html_file and not args.url:
        parser.error("provide a URL or --html-file PATH")

    service = load_delivery_service(ArticleExtractor())
    try:
        if args.html_file:
            service.deliver_html_file(
                args.html_file,
                title_override=args.title,
                dry_run=args.dry_run,
                save_to_bear=args.save_to_bear,
            )
        else:
            service.deliver_url(
                args.url,
                include_images=not args.no_images,
                dry_run=args.dry_run,
                save_to_bear=args.save_to_bear,
            )
    except Exception as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")


if __name__ == "__main__":
    main()
