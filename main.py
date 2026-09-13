#!/usr/bin/env python3
"""CLI entry point for the autonomous lead enrichment agent.

Usage:
    python main.py --domains postman.com supabase.com vapi.ai
    python main.py --input-file domains.txt --output out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from anthropic import Anthropic

from src.browser import BrowserSession
from src.config import settings
from src.cost_tracker import summarize
from src.pipeline import domain_cost, process_domain

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
    return parser.parse_args()


def load_domains(args: argparse.Namespace) -> list[str]:
    domains: list[str] = []
    if args.domains:
        domains.extend(args.domains)
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            domains.extend(line.strip() for line in f if line.strip())
    if not domains:
        parser_error = "Provide domains via --domains or --input-file."
        print(parser_error, file=sys.stderr)
        sys.exit(1)
    return domains


async def run(domains: list[str]) -> list[dict]:
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = Anthropic(api_key=settings.anthropic_api_key)
    records: list[dict] = []
    costs = []

    async with BrowserSession() as session:
        for domain in domains:
            logger.info("processing %s", domain)
            start = time.monotonic()
            try:
                result = await process_domain(session.browser, client, domain)
            except Exception as exc:  # noqa: BLE001 - absolute last line of defense
                logger.error("unhandled error processing %s: %s", domain, exc)
                result = None

            elapsed = time.monotonic() - start
            if result is None:
                records.append(
                    {
                        "domain": domain,
                        "intelligence": None,
                        "errors": [f"unhandled pipeline error"],
                        "elapsed_seconds": round(elapsed, 2),
                    }
                )
                continue

            costs.append(domain_cost(domain, result))
            records.append(
                {
                    "domain": domain,
                    "intelligence": result.intelligence.model_dump() if result.intelligence else None,
                    "pages_fetched": result.pages_fetched,
                    "pages_failed": result.pages_failed,
                    "errors": result.errors,
                    "tokens": {"input": result.input_tokens, "output": result.output_tokens},
                    "elapsed_seconds": round(elapsed, 2),
                }
            )
            logger.info(
                "done %s in %.1fs (%d pages ok, %d failed)",
                domain,
                elapsed,
                len(result.pages_fetched),
                len(result.pages_failed),
            )

    if costs:
        print("\n" + summarize(costs) + "\n")
    return records


def main() -> None:
    args = parse_args()
    domains = load_domains(args)
    records = asyncio.run(run(domains))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(records)} record(s) to {args.output}")


if __name__ == "__main__":
    main()
