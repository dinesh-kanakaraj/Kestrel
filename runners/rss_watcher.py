from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from rss_service import RSSService
from state_store import (
    StateStore,
)


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)

logger = logging.getLogger(
    "rss_watcher"
)


def set_github_output(
    name: str,
    value: str,
) -> None:
    output_path = os.getenv(
        "GITHUB_OUTPUT"
    )

    if not output_path:
        return

    with Path(output_path).open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            f"{name}={value}\n"
        )


def bootstrap() -> int:
    rss = RSSService()
    store = StateStore()

    articles = rss.get_articles()

    added = 0

    # Oldest first produces a more natural CSV.
    for article in reversed(
        articles
    ):
        if store.add_baseline_article(
            article
        ):
            added += 1

    store.mark_initialized()

    logger.info(
        "Bootstrap complete. "
        "%d article(s) marked as already seen.",
        added,
    )

    set_github_output(
        "new_articles",
        "0",
    )
    set_github_output(
        "has_discovered",
        "false",
    )

    return added


def run() -> int:
    rss = RSSService()
    store = StateStore()

    # Safety guard: scheduled GitHub runs can start
    # as soon as workflows are pushed. Until the user
    # performs the one-time bootstrap, do nothing.
    if not store.is_initialized():
        logger.warning(
            "State is not initialized. "
            "Run RSS Watcher manually with "
            "bootstrap=true before enabling "
            "normal automation."
        )

        set_github_output(
            "new_articles",
            "0",
        )
        set_github_output(
            "has_discovered",
            "false",
        )

        return 0

    articles = rss.get_articles()

    added = 0

    # Oldest unseen first.
    for article in reversed(
        articles
    ):
        if store.add_discovered_article(
            article
        ):
            added += 1

            logger.info(
                "DISCOVERED: %s",
                article.title,
            )

    has_discovered = bool(
        store.get_discovered_articles()
    )

    logger.info(
        "RSS watcher finished. "
        "new=%d has_discovered=%s",
        added,
        has_discovered,
    )

    set_github_output(
        "new_articles",
        str(added),
    )

    set_github_output(
        "has_discovered",
        (
            "true"
            if has_discovered
            else "false"
        ),
    )

    return added


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Mark current RSS items as "
            "already seen without generating "
            "tweets."
        ),
    )

    args = parser.parse_args()

    if args.bootstrap:
        bootstrap()
    else:
        run()


if __name__ == "__main__":
    main()
