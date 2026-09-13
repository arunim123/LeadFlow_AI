#!/usr/bin/env python3
"""CLI entry point for the autonomous lead enrichment agent.

Usage:
    python main.py --domains postman.com supabase.com vapi.ai
    python main.py --input-file domains.txt --output out.json --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from src.browser import BrowserSession, PlaywrightFetcher
from src.config import settings
from src.cost_tracker import summarize
from src.llm_client import AnthropicLLMClient
from src.pipeline import domain_cost, run_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous lead enrichment agent")
    parser.add_argument(
        "--domains", nargs="+", default=None, help="Space-separated list of domains to process."
    )
    parser.add_argument(
        "--input-file", default=None, help="Path to a text file with one domain per line."
    )
    parser.add_argument(
        "--output", default="output.json", help="Where to write the resulting JSON array."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Max domains processed at once (overrides DOMAIN_CONCURRENCY from .env).",
    )
    return parser.parse_args()


def load_domains(args: argparse.Namespace) -> list[str]:
    domains: list[str] = []
    if args.domains:
        domains.extend(args.domains)
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            domains.extend(line.strip() for line in f if line.strip())
    if not domains:
        print("Provide domains via --domains or --input-file.", file=sys.stderr)
        sys.exit(1)
    return domains


async def run(domains: list[str], concurrency: int | None) -> list[dict]:
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.",
            file=sys.stderr,
        )
        sys.exit(1)

    if concurrency is not None:
        # Settings is a frozen dataclass; this is the one sanctioned override
        # point for a CLI flag beating the .env default for a single run.
        object.__setattr__(settings, "domain_concurrency", concurrency)

    llm = AnthropicLLMClient(api_key=settings.anthropic_api_key)
    start_times: dict[str, float] = {}

    async with BrowserSession() as session:
        fetcher = PlaywrightFetcher(session.browser)
        start = time.monotonic()
        for d in domains:
            start_times[d] = start
        logger.info("processing %d domain(s) with concurrency=%d", len(domains), settings.domain_concurrency)
        results_by_domain = await run_batch(fetcher, llm, domains)
        elapsed_total = time.monotonic() - start

    records: list[dict] = []
    costs = []
    for domain in domains:  # preserve input order in the output
        result = results_by_domain.get(domain)
        if result is None:
            records.append({"domain": domain, "intelligence": None, "errors": ["no result produced"]})
            continue

        costs.append(domain_cost(domain, result))
        records.append(
            {
                "domain": domain,
                "intelligence": result.intelligence.model_dump() if result.intelligence else None,
                "pages_fetched": result.pages_fetched,
                "pages_failed": result.pages_failed,
                "tool_calls": result.tool_calls,
                "errors": result.errors,
                "tokens": {"input": result.input_tokens, "output": result.output_tokens},
            }
        )
        logger.info(
            "done %s (%d pages ok, %d failed, %d tool calls)",
            domain,
            len(result.pages_fetched),
            len(result.pages_failed),
            result.tool_calls,
        )

    logger.info("batch finished in %.1fs", elapsed_total)
    if costs:
        print("\n" + summarize(costs) + "\n")
    return records


def main() -> None:
    args = parse_args()
    domains = load_domains(args)
    records = asyncio.run(run(domains, args.concurrency))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(records)} record(s) to {args.output}")


if __name__ == "__main__":
    main()
