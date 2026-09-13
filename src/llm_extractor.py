"""Structured extraction via Claude tool-calling.

We don't ask the model to "return JSON" and hope — we hand it a tool
whose input_schema *is* CompanyIntelligence's JSON Schema and force
tool_choice, so the SDK gives us back a dict that already matches the
Pydantic model. Any docstring in schemas.py's Field(description=...)
directly becomes the field-level instruction the model sees.
"""

from __future__ import annotations

import logging

from anthropic import Anthropic, APIError

from src.config import settings
from src.schemas import CompanyIntelligence

logger = logging.getLogger(__name__)

_TOOL_NAME = "record_company_intelligence"

_SYSTEM_PROMPT = """\
You are a B2B lead-research analyst. You will be given cleaned text \
scraped from a company's own public website (homepage plus a few \
subpages such as about/team/pricing/contact), each section labeled \
with its source URL.

Extract ONLY what is actually supported by the provided text. Do not \
invent leadership names, emails, or LinkedIn URLs that are not present \
in the source text. If a field has no support in the text, leave it \
empty (empty string/list) rather than guessing.

Set data_confidence_score based on how complete the retrieved text \
actually was: fewer usable pages, missing team/leadership info, or no \
contact emails found should all pull the score down. A confidence of \
1.0 should be reserved for cases where overview, audience, contact \
info, and named leadership are all clearly present in the source text.
"""


def _tool_schema() -> dict:
    schema = CompanyIntelligence.model_json_schema()
    # The model shouldn't be asked to re-derive the domain — we already
    # know it from the crawl target — so drop it from the tool's input
    # schema and inject it after the call instead.
    schema["properties"].pop("domain", None)
    if "required" in schema:
        schema["required"] = [f for f in schema["required"] if f != "domain"]
    return schema


def extract_intelligence(
    client: Anthropic, domain: str, combined_context: str
) -> tuple[CompanyIntelligence | None, int, int]:
    """Call the LLM once with the pre-cleaned, combined page context.

    Returns (result_or_None, input_tokens, output_tokens). Never raises:
    a malformed/refused response is reported as `None` so the pipeline
    can fall back gracefully instead of the whole domain run crashing.
    """
    if not combined_context.strip():
        logger.warning("empty context for %s — skipping LLM call", domain)
        return None, 0, 0

    try:
        response = client.messages.create(
            model=settings.model_name,
            max_tokens=settings.max_output_tokens,
            system=_SYSTEM_PROMPT,
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Record the extracted company intelligence.",
                    "input_schema": _tool_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Target domain: {domain}\n\n"
                        f"--- Scraped site content (cleaned, truncated) ---\n"
                        f"{combined_context}"
                    ),
                }
            ],
        )
    except APIError as exc:
        logger.error("Anthropic API error for %s: %s", domain, exc)
        return None, 0, 0
    except Exception as exc:  # noqa: BLE001 - one bad domain must not kill the batch
        logger.error("unexpected LLM error for %s: %s", domain, exc)
        return None, 0, 0

    usage = response.usage
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    tool_use_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        logger.warning("no tool_use block returned for %s", domain)
        return None, input_tokens, output_tokens

    try:
        payload = dict(tool_use_block.input)
        payload["domain"] = domain
        result = CompanyIntelligence.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - schema drift, bad field, etc.
        logger.error("failed to validate LLM output for %s: %s", domain, exc)
        return None, input_tokens, output_tokens

    return result, input_tokens, output_tokens
