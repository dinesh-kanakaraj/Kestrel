from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from config import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferPostResult:
    post_id: str
    text: str
    status: str


class PostingService:
    """
    Buffer GraphQL posting client.

    The dispatcher controls WHEN a post is allowed.
    This service only performs the actual Buffer
    createPost call with mode=shareNow.
    """

    CREATE_POST_MUTATION = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        __typename

        ... on PostActionSuccess {
          post {
            id
            text
            status
            dueAt
          }
        }

        ... on MutationError {
          message
        }
      }
    }
    """

    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()

        if not self.settings.buffer_api_key:
            raise RuntimeError(
                "BUFFER_API_KEY is missing."
            )

        if not self.settings.buffer_channel_id:
            raise RuntimeError(
                "BUFFER_CHANNEL_ID is missing."
            )

    def post_now(
        self,
        tweet: str,
    ) -> BufferPostResult:
        tweet = (tweet or "").strip()

        if not tweet:
            raise ValueError(
                "Tweet cannot be empty."
            )

        variables = {
            "input": {
                "text": tweet,
                "channelId": (
                    self.settings
                    .buffer_channel_id
                ),
                "schedulingType": (
                    "automatic"
                ),
                "mode": "shareNow",
            }
        }

        # Deliberately no automatic retry here.
        # Retrying an ambiguous createPost timeout could
        # create a duplicate post because the Buffer API
        # does not document an idempotency key.
        response = requests.post(
            self.settings.buffer_api_url,
            headers={
                "Authorization": (
                    "Bearer "
                    f"{self.settings.buffer_api_key}"
                ),
                "Content-Type": (
                    "application/json"
                ),
            },
            json={
                "query": (
                    self.CREATE_POST_MUTATION
                ),
                "variables": variables,
            },
            timeout=(
                self.settings
                .buffer_request_timeout
            ),
        )

        response.raise_for_status()

        payload = response.json()

        if payload.get("errors"):
            raise RuntimeError(
                "Buffer GraphQL error: "
                f"{payload['errors']}"
            )

        result = (
            payload.get("data", {})
            .get("createPost")
        )

        if not result:
            raise RuntimeError(
                "Buffer returned no createPost "
                f"payload: {payload}"
            )

        if (
            result.get("__typename")
            != "PostActionSuccess"
        ):
            raise RuntimeError(
                "Buffer rejected the post: "
                f"{result.get('message', result)}"
            )

        post = result.get("post") or {}

        post_id = str(
            post.get("id") or ""
        ).strip()

        if not post_id:
            raise RuntimeError(
                "Buffer success response did not "
                "contain a post ID."
            )

        logger.info(
            "Buffer accepted post id=%s status=%s",
            post_id,
            post.get("status", ""),
        )

        return BufferPostResult(
            post_id=post_id,
            text=str(
                post.get("text")
                or tweet
            ),
            status=str(
                post.get("status")
                or ""
            ),
        )
