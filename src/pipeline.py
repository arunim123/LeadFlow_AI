"""Batch orchestration: runs the agent across many domains concurrently.

Concurrency is bounded by `settings.domain_concurrency` so a batch of,
say, 50 domains doesn't open 50 browser contexts and 50 simultaneous
Claude conversations at once. Each domain's agent run is wrapped in its
own try/except here too — belt-and-suspenders on top of the agent's own
internal resilience — so one truly unexpected exception can never take
the rest of the batch down with it.
"""

from __future__ import annotations

import asyncio
import logging

from src.agent import run_agent
from src.browser import PageFetcher
from src.config import settings
from src.cost_tracker import DomainCost
from src.llm_client import LLMClient
from src.schemas import DomainRunResult

logger = logging.getLogger(__name__)


async def run_batch(
    fetcher: PageFetcher, llm: LLMClient, domains: list[str]
) -> dict[str, DomainRunResult]:
    semaphore = asyncio.Semaphore(settings.domain_concurrency)
    results: dict[str, DomainRunResult] = {}

    async def _one(domain: str) -> None:
        async with semaphore:
            try:
                results[domain] = await run_agent(fetcher, llm, domain)
            except Exception as exc:  # noqa: BLE001 - absolute last line of defense
                logger.error("unhandled error running agent for %s: %s", domain, exc)
                failed = DomainRunResult()
                failed.errors.append(f"unhandled agent error: {exc}")
                results[domain] = failed

    await asyncio.gather(*(_one(d) for d in domains))
    return results


def domain_cost(domain: str, result: DomainRunResult) -> DomainCost:
    return DomainCost(domain=domain, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
