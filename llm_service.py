from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import errors, types
from groq import Groq

from config import Settings, get_settings
from rss_service import Article


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    tweet: str
    provider: str
    model: str


class GeminiDailyQuotaExhausted(
    RuntimeError
):
    pass


class LLMService:
    """
    Routing policy:

    1. Gemini first.
    2. Temporary Gemini 429 -> wait/retry Gemini.
    3. Gemini daily quota -> switch to Groq.
    4. After daily quota is detected, all remaining
       articles in the same process use Groq.
    5. Other Gemini errors are not silently converted
       into Groq fallback.
    """

    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()

        if not self.settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing."
            )

        if not self.settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is missing."
            )

        self.system_prompt = (
            self._load_system_prompt()
        )

        self.gemini_client = genai.Client(
            api_key=(
                self.settings
                .gemini_api_key
            )
        )

        self.groq_client = Groq(
            api_key=(
                self.settings
                .groq_api_key
            )
        )

        self.gemini_daily_exhausted = (
            False
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_tweet(
        self,
        article: Article,
    ) -> GenerationResult:
        user_prompt = self._build_user_prompt(
            article
        )

        if (
            not self.gemini_daily_exhausted
        ):
            try:
                tweet = (
                    self._generate_gemini(
                        user_prompt
                    )
                )

                return GenerationResult(
                    tweet=tweet,
                    provider="gemini",
                    model=(
                        self.settings
                        .gemini_model
                    ),
                )

            except GeminiDailyQuotaExhausted:
                self.gemini_daily_exhausted = (
                    True
                )

                logger.warning(
                    "Gemini daily quota exhausted. "
                    "Switching remaining items in "
                    "this run to Groq."
                )

        tweet = self._generate_groq(
            user_prompt
        )

        return GenerationResult(
            tweet=tweet,
            provider="groq",
            model=(
                self.settings
                .groq_model
            ),
        )

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        # Public GitHub repositories should keep the
        # real prompt in a GitHub Actions secret.
        secret_prompt = os.getenv(
            "TWEET_SYSTEM_PROMPT",
            "",
        ).strip()

        if secret_prompt:
            return secret_prompt

        # Local-development fallback.
        path = (
            self.settings
            .system_prompt_path
        )

        if not path.exists():
            raise FileNotFoundError(
                "System prompt not found. "
                "Set TWEET_SYSTEM_PROMPT or create "
                f"{path}"
            )

        prompt = path.read_text(
            encoding="utf-8"
        ).strip()

        if not prompt:
            raise ValueError(
                "System prompt is empty."
            )

        return prompt

    @staticmethod
    def _build_user_prompt(
        article: Article,
    ) -> str:
        return (
            "[TITLE]\n"
            f"{article.title}\n"
            "[/TITLE]\n\n"
            "[CONTENT]\n"
            f"{article.content}\n"
            "[/CONTENT]\n\n"
            "Generate the tweet according to "
            "the system instructions. Return "
            "only the final tweet text."
        )

    # ------------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------------

    def _generate_gemini(
        self,
        user_prompt: str,
    ) -> str:
        for attempt in range(
            self.settings
            .gemini_transient_retries
            + 1
        ):
            try:
                logger.info(
                    "Generating with Gemini: %s",
                    self.settings.gemini_model,
                )

                response = (
                    self.gemini_client
                    .models
                    .generate_content(
                        model=(
                            self.settings
                            .gemini_model
                        ),
                        contents=user_prompt,
                        config=(
                            types.GenerateContentConfig(
                                system_instruction=(
                                    self.system_prompt
                                ),
                                max_output_tokens=(
                                    self.settings
                                    .llm_max_output_tokens
                                ),
                                thinking_config=(
                                    types.ThinkingConfig(
                                        thinking_level=(
                                            self._gemini_thinking_level()
                                        )
                                    )
                                ),
                            )
                        ),
                    )
                )

                tweet = (
                    response.text
                    or ""
                ).strip()

                if not tweet:
                    raise RuntimeError(
                        "Gemini returned an empty "
                        "response."
                    )

                return tweet

            except errors.APIError as exc:
                if getattr(
                    exc,
                    "code",
                    None,
                ) != 429:
                    raise

                retry_delay = (
                    self._extract_retry_delay(
                        exc
                    )
                )

                if self._is_daily_quota_error(
                    exc,
                    retry_delay,
                ):
                    raise (
                        GeminiDailyQuotaExhausted(
                            str(exc)
                        )
                    ) from exc

                if (
                    attempt
                    >= self.settings
                    .gemini_transient_retries
                ):
                    raise

                delay = (
                    retry_delay
                    if retry_delay
                    is not None
                    else min(
                        60.0,
                        2 ** attempt,
                    )
                    + random.uniform(
                        0,
                        0.5,
                    )
                )

                # Small cushion around the exact
                # retry boundary returned by Gemini.
                delay += 0.25

                logger.warning(
                    "Temporary Gemini 429. "
                    "Retry %d/%d in %.2fs.",
                    attempt + 1,
                    self.settings
                    .gemini_transient_retries,
                    delay,
                )

                time.sleep(delay)

        raise RuntimeError(
            "Gemini generation failed."
        )

    def _gemini_thinking_level(
        self,
    ) -> types.ThinkingLevel:
        mapping = {
            "minimal": (
                types.ThinkingLevel.MINIMAL
            ),
            "low": (
                types.ThinkingLevel.LOW
            ),
            "medium": (
                types.ThinkingLevel.MEDIUM
            ),
            "high": (
                types.ThinkingLevel.HIGH
            ),
        }

        try:
            return mapping[
                self.settings
                .gemini_thinking_level
            ]
        except KeyError as exc:
            raise ValueError(
                "GEMINI_THINKING_LEVEL must "
                "be minimal, low, medium, or high."
            ) from exc

    def _is_daily_quota_error(
        self,
        exc: errors.APIError,
        retry_delay: float | None,
    ) -> bool:
        text = self._gemini_error_text(
            exc
        )

        daily_markers = (
            "perday",
            "per_day",
            "per day",
            "requestsperday",
            "tokensperday",
            "requests per day",
            "tokens per day",
            "rpd",
            "tpd",
        )

        if any(
            marker in text
            for marker in daily_markers
        ):
            return True

        # A retry delay measured in many minutes/hours
        # is not a normal RPM/TPM throttle. Treat it as
        # daily exhaustion so the fallback is useful.
        if (
            retry_delay is not None
            and retry_delay >= 600
        ):
            return True

        return False

    def _extract_retry_delay(
        self,
        exc: errors.APIError,
    ) -> float | None:
        text = self._gemini_error_text(
            exc
        )

        patterns = (
            r'"retrydelay"\s*:\s*"([0-9.]+)s"',
            r"retrydelay[^0-9]*([0-9.]+)s",
            r"retry in\s+([0-9.]+)s",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                try:
                    return max(
                        0.0,
                        float(
                            match.group(1)
                        ),
                    )
                except ValueError:
                    pass

        return None

    def _gemini_error_text(
        self,
        exc: errors.APIError,
    ) -> str:
        pieces = [
            str(exc),
            str(
                getattr(
                    exc,
                    "message",
                    "",
                )
                or ""
            ),
        ]

        details = getattr(
            exc,
            "details",
            None,
        )

        if details is not None:
            try:
                pieces.append(
                    json.dumps(
                        details,
                        ensure_ascii=False,
                        default=str,
                    )
                )
            except Exception:
                pieces.extend(
                    self._flatten_strings(
                        details
                    )
                )

        return " ".join(
            pieces
        ).lower()

    def _flatten_strings(
        self,
        value: Any,
    ) -> list[str]:
        result: list[str] = []

        if isinstance(value, dict):
            for key, item in value.items():
                result.append(str(key))
                result.extend(
                    self._flatten_strings(
                        item
                    )
                )

        elif isinstance(value, list):
            for item in value:
                result.extend(
                    self._flatten_strings(
                        item
                    )
                )

        elif value is not None:
            result.append(str(value))

        return result

    # ------------------------------------------------------------------
    # Groq
    # ------------------------------------------------------------------

    def _generate_groq(
        self,
        user_prompt: str,
    ) -> str:
        logger.info(
            "Generating with Groq: %s",
            self.settings.groq_model,
        )

        response = (
            self.groq_client
            .chat
            .completions
            .create(
                model=(
                    self.settings
                    .groq_model
                ),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            self.system_prompt
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                max_completion_tokens=(
                    self.settings
                    .llm_max_output_tokens
                ),
                reasoning_effort=(
                    self.settings
                    .groq_reasoning_effort
                ),
                include_reasoning=False,
                stream=False,
            )
        )

        tweet = (
            response
            .choices[0]
            .message
            .content
            or ""
        ).strip()

        if not tweet:
            raise RuntimeError(
                "Groq returned an empty "
                "response."
            )

        return tweet
