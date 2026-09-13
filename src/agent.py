"""The autonomous per-domain research agent.

Unlike a fixed crawl-then-extract pipeline, this gives Claude three
tools (`visit_page`, `search_web`, `submit_intelligence`) and lets it
decide, turn by turn, which pages are worth reading and when it has
enough to stop:

- A step budget bounds cost/latency (`max_agent_steps`).
- A same-domain check on every `visit_page` call keeps the agent from
  wandering off-site.
- A failed `submit_intelligence` validation is surfaced back to the
  model as a normal tool error (`is_error=True`) so it can self-correct
  in the same run instead of the whole domain failing outright.
- If the step budget runs out before a valid submission, one last call
  forces `tool_choice` to `submit_intelligence` so the run still ends
  with *some* structured record rather than nothing.
- Multiple tool calls requested in a single turn (e.g. the model asking
  to visit three pages at once) are executed concurrently.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from src.browser import PageFetcher
from src.config import settings
from src.llm_client import LLMClient
from src.schemas import CompanyIntelligence, DomainRunResult
from src.tools import (
    SEARCH_WEB_TOOL,
    VISIT_PAGE_TOOL,
    ToolOutcome,
    execute_search_web,
    execute_visit_page,
    submit_tool_schema,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an autonomous B2B lead-research agent. Given a target company \
domain, your job is to explore its public website using the visit_page \
tool and build a structured intelligence record about the company.

Guidelines:
- Start with the homepage, then follow links that look like about/team/ \
leadership/company/contact/pricing pages. You typically need 2-5 pages \
total — stop once you have enough, don't try to visit everything.
- Only extract information actually present in what you've read. Never \
invent leadership names, emails, or LinkedIn URLs.
- If a named leader has no LinkedIn URL on the site, you may use \
search_web once per person to try to find one — this is optional and \
skippable if it's not configured.
- Don't visit the same page twice — you'll be shown cached content if \
you try.
- Call submit_intelligence exactly once, when you're done, with your \
best-effort record. Set data_confidence_score based on how complete \
what you actually found was — missing leadership, no contact email, or \
a blocked/unreachable site should all lower it.
- You have a limited number of tool calls remaining, so be efficient.
"""


async def run_agent(
    fetcher: PageFetcher, llm: LLMClient, domain: str, *, max_steps: int | None = None
) -> DomainRunResult:
    max_steps = max_steps if max_steps is not None else settings.max_agent_steps
    result = DomainRunResult()
    visited_cache: dict[str, str] = {}
    tools = [VISIT_PAGE_TOOL, SEARCH_WEB_TOOL, submit_tool_schema()]

    messages: list[dict] = [
        {
            "role": "user",
            "content": f"Target domain: {domain}\nStart by visiting https://{domain}/",
        }
    ]

    for step in range(max_steps):
        try:
            response = await llm.create(
                model=settings.model_name,
                max_tokens=settings.max_output_tokens,
                system=_SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - one domain's LLM outage must not kill the batch
            result.errors.append(f"LLM call failed at step {step}: {exc}")
            break

        _accumulate_usage(result, response)
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            result.errors.append("model responded without a tool call; ending run")
            break

        submit_blocks = [b for b in tool_uses if b.name == "submit_intelligence"]
        action_blocks = [b for b in tool_uses if b.name != "submit_intelligence"]

        tool_result_blocks: list[dict] = []

        if action_blocks:

            async def _run(block):
                if block.name == "visit_page":
                    outcome = await execute_visit_page(
                        fetcher, domain, block.input.get("url", ""), visited_cache
                    )
                elif block.name == "search_web":
                    outcome = await execute_search_web(block.input.get("query", ""))
                else:
                    outcome = ToolOutcome(tool_result_text=f"Unknown tool: {block.name}", is_error=True)
                return block, outcome

            pairs = await asyncio.gather(*(_run(b) for b in action_blocks))
            for block, outcome in pairs:
                result.tool_calls += 1
                if outcome.fetched_url:
                    target_list = result.pages_fetched if outcome.ok else result.pages_failed
                    if outcome.fetched_url not in target_list:
                        target_list.append(outcome.fetched_url)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.tool_result_text,
                        "is_error": outcome.is_error,
                    }
                )

        finished = False
        for block in submit_blocks:
            result.tool_calls += 1
            try:
                payload = dict(block.input)
                payload["domain"] = domain
                result.intelligence = CompanyIntelligence.model_validate(payload)
                finished = True
            except ValidationError as exc:
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Invalid submission, please fix and resubmit: {exc}",
                        "is_error": True,
                    }
                )

        if finished:
            break

        if not tool_result_blocks:
            result.errors.append("no tool results to return; ending run to avoid a stuck loop")
            break

        messages.append({"role": "user", "content": tool_result_blocks})

    if result.intelligence is None:
        result.errors.append(
            f"exhausted {max_steps} steps without a valid submission; forcing best-effort finalization"
        )
        result.intelligence = await _force_finalize(llm, domain, messages, result)

    return result


def _accumulate_usage(result: DomainRunResult, response) -> None:
    usage = getattr(response, "usage", None)
    result.input_tokens += getattr(usage, "input_tokens", 0) or 0
    result.output_tokens += getattr(usage, "output_tokens", 0) or 0


async def _force_finalize(
    llm: LLMClient, domain: str, messages: list[dict], result: DomainRunResult
) -> CompanyIntelligence:
    """Last resort: force the tool choice so the run still ends with a structured record."""
    try:
        response = await llm.create(
            model=settings.model_name,
            max_tokens=settings.max_output_tokens,
            system=_SYSTEM_PROMPT
            + "\nYou are out of exploration budget — submit your best-effort record NOW.",
            tools=[submit_tool_schema()],
            tool_choice={"type": "tool", "name": "submit_intelligence"},
            messages=messages,
        )
        _accumulate_usage(result, response)
        block = next(b for b in response.content if getattr(b, "type", None) == "tool_use")
        payload = dict(block.input)
        payload["domain"] = domain
        return CompanyIntelligence.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - absolute last line of defense
        result.errors.append(f"forced finalization also failed: {exc}")
        return CompanyIntelligence(
            domain=domain,
            company_overview="Unable to extract — agent exhausted its step budget without a valid submission.",
            target_audience="",
            contact_points=[],
            leadership=[],
            data_confidence_score=0.0,
        )
