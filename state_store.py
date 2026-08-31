from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config import Settings, get_settings
from rss_service import Article


logger = logging.getLogger(__name__)


STATUS_DISCOVERED = "DISCOVERED"
STATUS_READY = "READY"
STATUS_POSTED = "POSTED"

VALID_STATUSES = {
    STATUS_DISCOVERED,
    STATUS_READY,
    STATUS_POSTED,
}

CSV_FIELDS = [
    "id",
    "title",
    "url",
    "published_datetime",
    "status",
    "tweet",
    "llm_provider",
    "discovered_datetime",
    "tweet_generated_datetime",
    "tweet_posted_datetime",
]


class StateStore:
    """
    CSV-backed state store.

    Each article lives in the CSV file for the date on
    which it was discovered. Later status updates modify
    that same row, even if generation/posting happens on
    another day.
    """

    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.state_dir = (
            self.settings.state_dir
        )
        self.local_tz = ZoneInfo(
            self.settings.timezone
        )

        self.state_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    # ------------------------------------------------------------------
    # Initialization guard
    # ------------------------------------------------------------------

    @property
    def initialization_marker(self) -> Path:
        return self.state_dir / ".initialized"

    def is_initialized(self) -> bool:
        return self.initialization_marker.exists()

    def mark_initialized(self) -> None:
        self.state_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.initialization_marker.write_text(
            self.now_iso() + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def now_local(self) -> datetime:
        return datetime.now(
            self.local_tz
        )

    def now_iso(self) -> str:
        return self.now_local().isoformat(
            timespec="seconds"
        )

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def today_log_path(self) -> Path:
        return self.state_dir / (
            "tweet_log_"
            f"{self.now_local():%Y-%m-%d}"
            ".csv"
        )

    def log_paths(
        self,
        reverse: bool = False,
    ) -> list[Path]:
        return sorted(
            self.state_dir.glob(
                "tweet_log_*.csv"
            ),
            reverse=reverse,
        )

    def _ensure_file(
        self,
        path: Path,
    ) -> None:
        if path.exists():
            return

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=CSV_FIELDS,
            )
            writer.writeheader()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def all_rows(
        self,
    ) -> list[dict[str, str]]:
        rows: list[
            dict[str, str]
        ] = []

        for path in self.log_paths():
            try:
                with path.open(
                    "r",
                    newline="",
                    encoding="utf-8-sig",
                ) as file:
                    reader = csv.DictReader(
                        file
                    )

                    for row in reader:
                        normalized = {
                            field: (
                                row.get(
                                    field,
                                    "",
                                )
                                or ""
                            )
                            for field
                            in CSV_FIELDS
                        }

                        normalized[
                            "_path"
                        ] = str(path)

                        rows.append(
                            normalized
                        )

            except (
                OSError,
                csv.Error,
            ) as exc:
                raise RuntimeError(
                    "Failed reading state "
                    f"file {path}: {exc}"
                ) from exc

        return rows

    def article_exists(
        self,
        article_id: str,
    ) -> bool:
        return any(
            row["id"] == article_id
            for row in self.all_rows()
        )

    def rows_by_status(
        self,
        status: str,
    ) -> list[dict[str, str]]:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status: {status}"
            )

        rows = [
            row
            for row in self.all_rows()
            if row["status"] == status
        ]

        return sorted(
            rows,
            key=self._queue_sort_key,
        )

    def get_discovered_articles(
        self,
    ) -> list[dict[str, str]]:
        return self.rows_by_status(
            STATUS_DISCOVERED
        )

    def get_ready_articles(
        self,
    ) -> list[dict[str, str]]:
        return self.rows_by_status(
            STATUS_READY
        )

    def get_oldest_ready(
        self,
    ) -> dict[str, str] | None:
        ready = self.get_ready_articles()
        return ready[0] if ready else None

    def get_last_posted_datetime(
        self,
    ) -> datetime | None:
        posted_times: list[
            datetime
        ] = []

        for row in self.all_rows():
            value = row[
                "tweet_posted_datetime"
            ].strip()

            if not value:
                continue

            try:
                dt = datetime.fromisoformat(
                    value
                )

                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=self.local_tz
                    )

                posted_times.append(dt)

            except ValueError:
                logger.warning(
                    "Ignoring invalid posted "
                    "datetime for article %s: %r",
                    row["id"],
                    value,
                )

        return (
            max(posted_times)
            if posted_times
            else None
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add_discovered_article(
        self,
        article: Article,
    ) -> bool:
        if self.article_exists(
            article.id
        ):
            return False

        row = {
            "id": article.id,
            "title": article.title,
            "url": article.url,
            "published_datetime": (
                article.published_datetime
            ),
            "status": STATUS_DISCOVERED,
            "tweet": "",
            "llm_provider": "",
            "discovered_datetime": (
                self.now_iso()
            ),
            "tweet_generated_datetime": "",
            "tweet_posted_datetime": "",
        }

        self._append_row(
            self.today_log_path(),
            row,
        )

        return True

    def add_baseline_article(
        self,
        article: Article,
    ) -> bool:
        """
        One-time bootstrap.

        Baseline items are stored as POSTED with blank
        tweet/timestamps. This means they are considered
        already seen but do not affect the 30-minute
        posting clock.
        """
        if self.article_exists(
            article.id
        ):
            return False

        row = {
            "id": article.id,
            "title": article.title,
            "url": article.url,
            "published_datetime": (
                article.published_datetime
            ),
            "status": STATUS_POSTED,
            "tweet": "",
            "llm_provider": "",
            "discovered_datetime": (
                self.now_iso()
            ),
            "tweet_generated_datetime": "",
            "tweet_posted_datetime": "",
        }

        self._append_row(
            self.today_log_path(),
            row,
        )

        return True

    def mark_ready(
        self,
        article_id: str,
        tweet: str,
        llm_provider: str,
    ) -> None:
        tweet = (tweet or "").strip()

        if not tweet:
            raise ValueError(
                "Tweet cannot be empty."
            )

        self._update_article(
            article_id,
            {
                "status": STATUS_READY,
                "tweet": tweet,
                "llm_provider": (
                    llm_provider
                ),
                "tweet_generated_datetime": (
                    self.now_iso()
                ),
            },
        )

    def mark_posted(
        self,
        article_id: str,
        posted_datetime: str | None = None,
    ) -> None:
        self._update_article(
            article_id,
            {
                "status": STATUS_POSTED,
                "tweet_posted_datetime": (
                    posted_datetime
                    or self.now_iso()
                ),
            },
        )

    # ------------------------------------------------------------------
    # Internal CSV mutation
    # ------------------------------------------------------------------

    def _append_row(
        self,
        path: Path,
        row: dict[str, str],
    ) -> None:
        self._ensure_file(path)

        with path.open(
            "a",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=CSV_FIELDS,
            )

            writer.writerow(
                {
                    field: row.get(
                        field,
                        "",
                    )
                    for field
                    in CSV_FIELDS
                }
            )

    def _update_article(
        self,
        article_id: str,
        updates: dict[str, str],
    ) -> None:
        for path in self.log_paths(
            reverse=True
        ):
            with path.open(
                "r",
                newline="",
                encoding="utf-8-sig",
            ) as file:
                rows = list(
                    csv.DictReader(file)
                )

            found = False

            for row in rows:
                if (
                    row.get(
                        "id",
                        ""
                    )
                    != article_id
                ):
                    continue

                row.update(updates)
                found = True
                break

            if not found:
                continue

            temp_path = path.with_suffix(
                path.suffix + ".tmp"
            )

            with temp_path.open(
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=CSV_FIELDS,
                )
                writer.writeheader()

                for row in rows:
                    writer.writerow(
                        {
                            field: row.get(
                                field,
                                "",
                            )
                            for field
                            in CSV_FIELDS
                        }
                    )

            temp_path.replace(path)
            return

        raise KeyError(
            "Article not found in state: "
            f"{article_id}"
        )

    def _queue_sort_key(
        self,
        row: dict[str, str],
    ) -> tuple[datetime, datetime]:
        published = self._safe_datetime(
            row.get(
                "published_datetime",
                "",
            )
        )

        discovered = self._safe_datetime(
            row.get(
                "discovered_datetime",
                "",
            )
        )

        return (
            published,
            discovered,
        )

    def _safe_datetime(
        self,
        value: str,
    ) -> datetime:
        if value:
            try:
                dt = datetime.fromisoformat(
                    value
                )

                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=self.local_tz
                    )

                return dt

            except ValueError:
                pass

        return datetime.max.replace(
            tzinfo=timezone.utc
        )
