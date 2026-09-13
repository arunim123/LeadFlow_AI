"""LLM client wrapper: retry/backoff on rate limits and transient 5xxs.

Wrapped as `LLMClient` (a Protocol with a single async `create(**kwargs)`
method) rather than used directly, so tests can inject a scripted fake
client and drive the agent loop deterministically without ever calling
the real API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import anthropic
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        header = response.headers.get("retry-after")
        return float(header) if header else None
    except (TypeError, ValueError, AttributeError):
        return None


class AnthropicLLMClient:
    """Thin wrapper around AsyncAnthropic adding retry/backoff.

    - `anthropic.RateLimitError` (429): honors the `retry-after` header
      when present, otherwise exponential backoff.
    - `anthropic.APIStatusError` with a 5xx status: exponential backoff
      (transient server-side issue, worth retrying).
    - Anything else (4xx other than rate limit, auth errors, etc.):
      raised immediately — retrying won't help and would just hide a
      real problem (bad API key, malformed request).
    """

    def __init__(self, api_key: str, max_retries: int | None = None) -> None:
        from src.config import settings  # local import avoids a config->client cycle at module load

        self._client = AsyncAnthropic(api_key=api_key)
        self._max_retries = max_retries if max_retries is not None else settings.llm_max_retries

    async def create(self, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                last_exc = exc
                delay = _retry_after_seconds(exc) or min(2**attempt, 30)
                logger.warning(
                    "rate limited (attempt %d/%d), sleeping %.1fs", attempt, self._max_retries, delay
                )
                await asyncio.sleep(delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code is not None and exc.status_code >= 500:
                    last_exc = exc
                    delay = min(2**attempt, 20)
                    logger.warning(
                        "server error %s (attempt %d/%d), sleeping %.1fs",
                        exc.status_code,
                        attempt,
                        self._max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        assert last_exc is not None
        raise last_exc
