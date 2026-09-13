"""Per-domain orchestration: crawl -> clean -> extract -> fallback.

The one rule this module exists to enforce: **a failure anywhere for
one domain must never take down the batch.** Every external call
(browser fetch, LLM call, search fallback) already returns a
soft-failure value on its own; this module's job is to keep collecting
partial results and errors into a DomainRunResult no matter what goes
wrong, and always produce *some* record for every input domain.
"""

from __future__ import annotations

import logging

from anthropic import Anthropic
from playwright.async_api import Browser

from src.browser import fetch_many, fetch_page
from src.config import settings
from src.content_cleaner import clean_html_to_text, extract_emails
from src.cost_tracker import DomainCost
from src.link_discovery import discover_subpages
from src.llm_extractor import extract_intelligence
from src.schemas import CompanyIntelligence, DomainRunResult
from src.search_fallback import enrich_missing_linkedin_urls

logger = logging.getLogger(__name__)


def _normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return domain.rstrip("/")


async def process_domain(
    browser: Browser, client: Anthropic, raw_domain: str
) -> DomainRunResult:
    domain = _normalize_domain(raw_domain)
    result = DomainRunResult()
    homepage_url = f"https://{domain}/"

    # --- Step 1: homepage ---
    home_fetch = await fetch_page(browser, homepage_url)
    if not home_fetch.ok:
        result.pages_failed.append(homepage_url)
        result.errors.append(f"homepage unreachable: {home_fetch.error}")
        # Still try the bare domain without https as a last resort before giving up.
        fallback_fetch = await fetch_page(browser, f"http://{domain}/")
        if not fallback_fetch.ok:
            result.errors.append(f"http fallback also failed: {fallback_fetch.error}")
            result.intelligence = CompanyIntelligence(
                domain=domain,
                company_overview="Unable to retrieve — site did not respond.",
                target_audience="",
                contact_points=[],
                leadership=[],
                data_confidence_score=0.0,
            )
            return result
        home_fetch = fallback_fetch

    result.pages_fetched.append(home_fetch.url)
    all_emails = set(extract_emails(home_fetch.html or ""))
    context_chunks = [
        f"### Source: {home_fetch.url}\n{clean_html_to_text(home_fetch.html or '')}"
    ]

    # --- Step 1b: discover + fetch subpages ---
    try:
        subpage_urls = discover_subpages(home_fetch.html or "", homepage_url)
    except Exception as exc:  # noqa: BLE001
        subpage_urls = []
        result.errors.append(f"link discovery failed: {exc}")

    if subpage_urls:
        sub_results = await fetch_many(browser, subpage_urls)
        for sub in sub_results:
            if sub.ok:
                result.pages_fetched.append(sub.url)
                all_emails.update(extract_emails(sub.html or ""))
                context_chunks.append(
                    f"### Source: {sub.url}\n{clean_html_to_text(sub.html or '')}"
                )
            else:
                result.pages_failed.append(sub.url)
                result.errors.append(f"{sub.url}: {sub.error}")

    # --- Step 2: token-budget the combined context ---
    combined_context = "\n\n".join(context_chunks)
    if len(combined_context) > settings.max_chars_total:
        combined_context = combined_context[: settings.max_chars_total] + "\n[...truncated...]"

    # --- Step 3: LLM structured extraction ---
    intelligence, in_tok, out_tok = extract_intelligence(client, domain, combined_context)
    result.input_tokens = in_tok
    result.output_tokens = out_tok

    if intelligence is None:
        result.errors.append("LLM extraction failed or returned no usable data")
        # Degrade gracefully: still emit a record so the domain has a row
        # in the output, with whatever we scraped directly (emails).
        intelligence = CompanyIntelligence(
            domain=domain,
            company_overview="Extraction failed — see errors for this domain.",
            target_audience="",
            contact_points=sorted(all_emails),
            leadership=[],
            data_confidence_score=0.1 if all_emails else 0.0,
        )
    else:
        # Merge in any emails regex-found directly, in case the LLM missed one.
        merged = sorted(set(intelligence.contact_points) | all_emails)
        intelligence.contact_points = merged

    # --- Step 4 (bonus): fill missing LinkedIn URLs via search fallback ---
    try:
        enrich_missing_linkedin_urls(intelligence, company_name=domain)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"search fallback failed: {exc}")

    result.intelligence = intelligence
    return result


def domain_cost(domain: str, result: DomainRunResult) -> DomainCost:
    return DomainCost(domain=domain, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
