from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

from config import get_settings
from posting_service import (
    PostingService,
)
from state_store import StateStore


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)

logger = logging.getLogger(
    "tweet_dispatcher"
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


def run() -> bool:
    settings = get_settings()
    store = StateStore(settings)

    article = store.get_oldest_ready()

    if article is None:
        logger.info(
            "No READY tweets."
        )

        set_github_output(
            "posted",
            "false",
        )
        set_github_output(
            "reason",
            "empty_queue",
        )

        return False

    now = store.now_local()
    last_posted = (
        store.get_last_posted_datetime()
    )

    if last_posted is not None:
        next_allowed = (
            last_posted
            + timedelta(
                minutes=(
                    settings
                    .min_tweet_gap_minutes
                )
            )
        )

        if now < next_allowed:
            remaining = (
                next_allowed - now
            )

            minutes_remaining = max(
                0,
                int(
                    (
                        remaining.total_seconds()
                        + 59
                    )
                    // 60
                ),
            )

            logger.info(
                "Posting gap not reached. "
                "Last post=%s next allowed=%s "
                "(~%d min remaining).",
                last_posted.isoformat(),
                next_allowed.isoformat(),
                minutes_remaining,
            )

            set_github_output(
                "posted",
                "false",
            )
            set_github_output(
                "reason",
                "gap_not_reached",
            )
            set_github_output(
                "minutes_remaining",
                str(minutes_remaining),
            )

            return False

    # Hard rule: a single dispatcher run can post
    # at most ONE tweet.
    posting = PostingService(
        settings
    )

    result = posting.post_now(
        article["tweet"]
    )

    store.mark_posted(
        article["id"],
        posted_datetime=(
            store.now_iso()
        ),
    )

    logger.info(
        "POSTED article=%s "
        "buffer_post_id=%s",
        article["title"],
        result.post_id,
    )

    set_github_output(
        "posted",
        "true",
    )
    set_github_output(
        "reason",
        "posted",
    )
    set_github_output(
        "article_id",
        article["id"],
    )
    set_github_output(
        "buffer_post_id",
        result.post_id,
    )

    return True


if __name__ == "__main__":
    run()
