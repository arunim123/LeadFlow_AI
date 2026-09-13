"""Tests for the agent's control-flow loop, using fakes for both the
browser fetch and the LLM call so the whole loop runs deterministically
with no network access and no API key.
"""

from __future__ import annotations

import pytest

from src.agent import run_agent
from src.browser import FetchResult


class FakeUsage:
    def __init__(self, inp: int = 10, out: int = 5):
        self.input_tokens = inp
        self.output_tokens = out


class FakeBlock:
    def __init__(self, type_: str, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class FakeResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage or FakeUsage()


class FakeFetcher:
    """Implements the PageFetcher protocol against an in-memory page map."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        html = self.pages.get(url)
        if html is None:
            return FetchResult(url=url, html=None, status=404, error="not found", error_type="not_found")
        return FetchResult(url=url, html=html, status=200)


class ScriptedLLM:
    """Implements the LLMClient protocol, returning one scripted response per call."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls = 0

    async def create(self, **kwargs) -> FakeResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        return self._responses.pop(0)


def tool_use(id_: str, name: str, input_: dict) -> FakeBlock:
    return FakeBlock("tool_use", id=id_, name=name, input=input_)


@pytest.mark.asyncio
async def test_agent_happy_path_visits_pages_then_submits():
    fetcher = FakeFetcher(
        {
            "https://acme.com/": "<html><body><a href='/about'>About us</a><p>Acme makes widgets.</p></body></html>",
            "https://acme.com/about": "<html><body><p>Founded by Jane Doe, CEO. contact@acme.com</p></body></html>",
        }
    )
    llm = ScriptedLLM(
        [
            FakeResponse([tool_use("1", "visit_page", {"url": "https://acme.com/"})]),
            FakeResponse([tool_use("2", "visit_page", {"url": "https://acme.com/about"})]),
            FakeResponse(
                [
                    tool_use(
                        "3",
                        "submit_intelligence",
                        {
                            "company_overview": "Acme makes widgets.",
                            "target_audience": "Widget buyers.",
                            "contact_points": ["contact@acme.com"],
                            "leadership": [
                                {"name": "Jane Doe", "role": "CEO", "linkedin_url": None, "source": "site"}
                            ],
                            "data_confidence_score": 0.9,
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_agent(fetcher, llm, "acme.com", max_steps=6)

    assert result.intelligence is not None
    assert result.intelligence.company_overview == "Acme makes widgets."
    assert result.intelligence.data_confidence_score == 0.9
    assert "https://acme.com/" in result.pages_fetched
    assert "https://acme.com/about" in result.pages_fetched
    assert result.tool_calls == 3
    assert not result.errors


@pytest.mark.asyncio
async def test_agent_rejects_off_domain_visit_without_fetching_it():
    fetcher = FakeFetcher({"https://acme.com/": "<html><body>hi</body></html>"})
    llm = ScriptedLLM(
        [
            FakeResponse([tool_use("1", "visit_page", {"url": "https://evil.com/"})]),
            FakeResponse(
                [
                    tool_use(
                        "2",
                        "submit_intelligence",
                        {
                            "company_overview": "Unknown.",
                            "target_audience": "Unknown.",
                            "contact_points": [],
                            "leadership": [],
                            "data_confidence_score": 0.1,
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_agent(fetcher, llm, "acme.com", max_steps=6)

    assert "https://evil.com/" not in fetcher.calls  # rejected before ever fetching
    assert result.intelligence.data_confidence_score == 0.1


@pytest.mark.asyncio
async def test_agent_self_repairs_invalid_submission():
    fetcher = FakeFetcher({"https://acme.com/": "<html><body>Acme.</body></html>"})
    llm = ScriptedLLM(
        [
            FakeResponse(
                [
                    tool_use(
                        "1",
                        "submit_intelligence",
                        {
                            "company_overview": "Acme.",
                            "target_audience": "Everyone.",
                            "contact_points": [],
                            "leadership": [],
                            "data_confidence_score": "high",  # invalid: should be a float
                        },
                    )
                ]
            ),
            FakeResponse(
                [
                    tool_use(
                        "2",
                        "submit_intelligence",
                        {
                            "company_overview": "Acme.",
                            "target_audience": "Everyone.",
                            "contact_points": [],
                            "leadership": [],
                            "data_confidence_score": 0.4,
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_agent(fetcher, llm, "acme.com", max_steps=6)

    assert result.intelligence.data_confidence_score == 0.4
    assert llm.calls == 2  # first (invalid) call + the corrected resubmission


@pytest.mark.asyncio
async def test_agent_forces_finalization_when_budget_exhausted():
    fetcher = FakeFetcher({"https://acme.com/": "<html><body>Acme.</body></html>"})
    # The model keeps re-requesting the same page and never submits.
    endless_visits = [
        FakeResponse([tool_use(str(i), "visit_page", {"url": "https://acme.com/"})]) for i in range(3)
    ]
    forced_final = FakeResponse(
        [
            tool_use(
                "final",
                "submit_intelligence",
                {
                    "company_overview": "Best effort.",
                    "target_audience": "Unknown.",
                    "contact_points": [],
                    "leadership": [],
                    "data_confidence_score": 0.2,
                },
            )
        ]
    )
    llm = ScriptedLLM(endless_visits + [forced_final])

    result = await run_agent(fetcher, llm, "acme.com", max_steps=3)

    assert result.intelligence.data_confidence_score == 0.2
    assert any("exhausted" in e for e in result.errors)
    # Repeated requests for the same URL should only hit the fetcher once —
    # subsequent ones are served from the in-run visited cache.
    assert fetcher.calls.count("https://acme.com/") == 1


@pytest.mark.asyncio
async def test_agent_survives_llm_outage_and_still_returns_a_record():
    fetcher = FakeFetcher({"https://acme.com/": "<html><body>Acme.</body></html>"})

    class DyingLLM:
        async def create(self, **kwargs):
            raise RuntimeError("simulated API outage")

    result = await run_agent(fetcher, DyingLLM(), "acme.com", max_steps=3)

    assert result.intelligence is not None
    assert result.intelligence.data_confidence_score == 0.0
    assert any("LLM call failed" in e for e in result.errors)
