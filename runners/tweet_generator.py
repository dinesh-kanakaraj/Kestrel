from __future__ import annotations

import logging
import os
from pathlib import Path

from llm_service import LLMService
from rss_service import RSSService
from state_store import StateStore


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)

logger = logging.getLogger(
    "tweet_generator"
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


def run() -> int:
    store = StateStore()

    discovered = (
        store.get_discovered_articles()
    )

    if not discovered:
        logger.info(
            "No DISCOVERED articles."
        )

        set_github_output(
            "generated_count",
            "0",
        )
        set_github_output(
            "has_ready",
            (
                "true"
                if store.get_ready_articles()
                else "false"
            ),
        )

        return 0

    rss = RSSService()
    llm = LLMService()

    generated = 0

    for row in discovered:
        logger.info(
            "Generating tweet for: %s",
            row["title"],
        )

        try:
            article = (
                rss.get_article_by_id(
                    article_id=row["id"],
                    fallback_url=row["url"],
                    fallback_title=(
                        row["title"]
                    ),
                    fallback_published_datetime=(
                        row[
                            "published_datetime"
                        ]
                    ),
                )
            )

            result = (
                llm.generate_tweet(
                    article
                )
            )

            store.mark_ready(
                article_id=row["id"],
                tweet=result.tweet,
                llm_provider=(
                    result.provider
                ),
            )

            generated += 1

            logger.info(
                "READY via %s/%s: %s",
                result.provider,
                result.model,
                row["title"],
            )

        except Exception:
            # Keep the row DISCOVERED so the next
            # RSS watcher can trigger another generator
            # attempt. Do not lose the item.
            logger.exception(
                "Generation failed for %s. "
                "Leaving status DISCOVERED.",
                row["title"],
            )

    has_ready = bool(
        store.get_ready_articles()
    )

    set_github_output(
        "generated_count",
        str(generated),
    )

    set_github_output(
        "has_ready",
        (
            "true"
            if has_ready
            else "false"
        ),
    )

    logger.info(
        "Tweet generator finished. "
        "generated=%d has_ready=%s",
        generated,
        has_ready,
    )

    return generated


if __name__ == "__main__":
    run()
