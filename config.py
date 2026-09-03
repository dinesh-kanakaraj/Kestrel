from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # RSS
    rss_feed_url: str
    rss_request_timeout: int
    rss_max_retries: int
    rss_user_agent: str

    # State
    state_dir: Path
    timezone: str
    min_tweet_gap_minutes: int

    # Prompt / LLM
    system_prompt_path: Path
    gemini_api_key: str | None
    gemini_model: str
    gemini_thinking_level: str
    gemini_transient_retries: int

    groq_api_key: str | None
    groq_model: str
    groq_reasoning_effort: str

    llm_max_output_tokens: int

    # Buffer
    buffer_api_url: str
    buffer_api_key: str | None
    buffer_channel_id: str | None
    buffer_request_timeout: int


def get_settings() -> Settings:
    return Settings(
        rss_feed_url=os.getenv(
            "RSS_FEED_URL",
            "https://barcauniversal.com/feed/",
        ),
        rss_request_timeout=_env_int(
            "RSS_REQUEST_TIMEOUT",
            30,
        ),
        rss_max_retries=_env_int(
            "RSS_MAX_RETRIES",
            4,
        ),
        rss_user_agent=os.getenv(
            "RSS_USER_AGENT",
            "TweegenRSSBot/1.0",
        ),
        state_dir=Path(
            os.getenv(
                "STATE_DIR",
                "logs",
            )
        ),
        timezone=os.getenv(
            "LOCAL_TIMEZONE",
            "Asia/Kolkata",
        ),
        min_tweet_gap_minutes=_env_int(
            "MIN_TWEET_GAP_MINUTES",
            30,
        ),
        system_prompt_path=Path(
            os.getenv(
                "SYSTEM_PROMPT_PATH",
                "prompts/system_prompt.txt",
            )
        ),
        gemini_api_key=os.getenv(
            "GEMINI_API_KEY"
        ),
        gemini_model=os.getenv(
            "GEMINI_MODEL",
            "gemini-3.6-flash",
        ),
        gemini_thinking_level=os.getenv(
            "GEMINI_THINKING_LEVEL",
            "low",
        ).lower(),
        gemini_transient_retries=_env_int(
            "GEMINI_TRANSIENT_RETRIES",
            3,
        ),
        groq_api_key=os.getenv(
            "GROQ_API_KEY"
        ),
        groq_model=os.getenv(
            "GROQ_MODEL",
            "openai/gpt-oss-20b",
        ),
        groq_reasoning_effort=os.getenv(
            "GROQ_REASONING_EFFORT",
            "low",
        ).lower(),
        llm_max_output_tokens=_env_int(
            "LLM_MAX_OUTPUT_TOKENS",
            280,
        ),
        buffer_api_url=os.getenv(
            "BUFFER_API_URL",
            "https://api.buffer.com",
        ),
        buffer_api_key=os.getenv(
            "BUFFER_API_KEY"
        ),
        buffer_channel_id=os.getenv(
            "BUFFER_CHANNEL_ID"
        ),
        buffer_request_timeout=_env_int(
            "BUFFER_REQUEST_TIMEOUT",
            30,
        ),
    )
