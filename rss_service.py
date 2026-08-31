from __future__ import annotations

import html
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from config import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    author: str
    published_datetime: str
    content: str


class RSSService:
    RETRYABLE_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.local_tz = ZoneInfo(
            self.settings.timezone
        )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    self.settings.rss_user_agent
                ),
                "Accept": (
                    "application/rss+xml, "
                    "application/xml, "
                    "text/xml, "
                    "*/*;q=0.8"
                ),
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_articles(self) -> list[Article]:
        body = self._fetch_bytes(
            self.settings.rss_feed_url
        )

        feed = feedparser.parse(body)

        if feed.bozo:
            logger.warning(
                "RSS parser warning: %s",
                feed.bozo_exception,
            )

        articles: list[Article] = []

        for entry in feed.entries:
            article = self._parse_entry(entry)

            if article is not None:
                articles.append(article)

        logger.info(
            "RSS returned %d usable article(s).",
            len(articles),
        )

        return articles

    def get_article_by_id(
        self,
        article_id: str,
        fallback_url: str,
        fallback_title: str,
        fallback_published_datetime: str = "",
    ) -> Article:
        """
        Re-fetch the RSS and look for the article.

        If it has already fallen out of the RSS window,
        scrape the article URL as a fallback.

        Full article content is returned only in memory.
        """
        for article in self.get_articles():
            if article.id == article_id:
                return article

        logger.warning(
            "Article %s no longer found in RSS. "
            "Falling back to page extraction.",
            article_id,
        )

        page_html = self._fetch_bytes(
            fallback_url
        ).decode(
            "utf-8",
            errors="replace",
        )

        content = trafilatura.extract(
            page_html,
            include_comments=False,
            include_tables=False,
            include_links=False,
            include_images=False,
        )

        if not content:
            raise RuntimeError(
                "Could not extract article content "
                f"from {fallback_url}"
            )

        return Article(
            id=article_id,
            title=fallback_title,
            url=fallback_url,
            author="",
            published_datetime=(
                fallback_published_datetime
            ),
            content=self._normalize_whitespace(
                content
            ),
        )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_entry(
        self,
        entry: Any,
    ) -> Article | None:
        article_id = self._normalize_whitespace(
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
            or ""
        )

        title = self._normalize_whitespace(
            entry.get("title", "")
        )

        url = self._normalize_whitespace(
            entry.get("link", "")
        )

        author = self._normalize_whitespace(
            entry.get("author", "")
        )

        if not article_id or not title or not url:
            logger.warning(
                "Skipping malformed RSS entry: "
                "id=%r title=%r url=%r",
                article_id,
                title,
                url,
            )
            return None

        raw_content = ""

        content_blocks = entry.get("content")

        if content_blocks:
            first = content_blocks[0]

            if isinstance(first, dict):
                raw_content = first.get(
                    "value",
                    "",
                )
            else:
                raw_content = getattr(
                    first,
                    "value",
                    "",
                )

        # Barca Universal currently provides full content
        # through content:encoded. Keep summary only as
        # a defensive fallback.
        if not raw_content:
            raw_content = entry.get(
                "summary",
                "",
            )

        content = self._html_to_text(
            raw_content
        )

        if not content:
            logger.warning(
                "Skipping RSS entry with no content: %s",
                title,
            )
            return None

        published = (
            entry.get("published")
            or entry.get("updated")
            or ""
        )

        published_datetime = (
            self._parse_datetime_local(
                published
            )
        )

        return Article(
            id=article_id,
            title=title,
            url=url,
            author=author,
            published_datetime=published_datetime,
            content=content,
        )

    def _parse_datetime_local(
        self,
        value: str,
    ) -> str:
        if not value:
            return ""

        try:
            dt = parsedate_to_datetime(
                value
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return (
                dt.astimezone(
                    self.local_tz
                )
                .isoformat(
                    timespec="seconds"
                )
            )

        except (
            TypeError,
            ValueError,
            OverflowError,
        ):
            logger.warning(
                "Could not parse RSS date: %r",
                value,
            )
            return ""

    @staticmethod
    def _normalize_whitespace(
        text: str,
    ) -> str:
        text = html.unescape(text or "")
        text = text.replace(
            "\xa0",
            " ",
        )
        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )
        text = re.sub(
            r"\n[ \t]+",
            "\n",
            text,
        )
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )
        return text.strip()

    def _html_to_text(
        self,
        raw_html: str,
    ) -> str:
        if not raw_html:
            return ""

        soup = BeautifulSoup(
            raw_html,
            "html.parser",
        )

        for tag in soup(
            [
                "script",
                "style",
                "noscript",
            ]
        ):
            tag.decompose()

        return self._normalize_whitespace(
            soup.get_text(
                "\n",
                strip=True,
            )
        )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _fetch_bytes(
        self,
        url: str,
    ) -> bytes:
        last_exception: Exception | None = None

        for attempt in range(
            self.settings.rss_max_retries + 1
        ):
            try:
                response = self.session.get(
                    url,
                    timeout=(
                        self.settings
                        .rss_request_timeout
                    ),
                    allow_redirects=True,
                )

                if (
                    response.status_code
                    in self.RETRYABLE_STATUS_CODES
                ):
                    if (
                        attempt
                        >= self.settings
                        .rss_max_retries
                    ):
                        response.raise_for_status()

                    retry_after = (
                        response.headers.get(
                            "Retry-After"
                        )
                    )

                    try:
                        delay = float(
                            retry_after
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        delay = min(
                            60.0,
                            2 ** attempt,
                        ) + random.uniform(
                            0,
                            0.5,
                        )

                    logger.warning(
                        "HTTP %s from %s. "
                        "Retrying in %.2fs.",
                        response.status_code,
                        url,
                        delay,
                    )

                    time.sleep(delay)
                    continue

                response.raise_for_status()
                return response.content

            except requests.RequestException as exc:
                last_exception = exc

                if (
                    attempt
                    >= self.settings
                    .rss_max_retries
                ):
                    raise

                delay = min(
                    60.0,
                    2 ** attempt,
                ) + random.uniform(
                    0,
                    0.5,
                )

                logger.warning(
                    "Request failed for %s: %s. "
                    "Retrying in %.2fs.",
                    url,
                    exc,
                    delay,
                )

                time.sleep(delay)

        if last_exception:
            raise last_exception

        raise RuntimeError(
            f"Unexpected fetch failure: {url}"
        )
