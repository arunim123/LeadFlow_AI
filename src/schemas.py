"""Structured-output schemas.

These Pydantic models double as the JSON Schema handed to the LLM as a
tool definition (via `model_json_schema()`), so the model is forced to
return exactly this shape instead of free-form prose we'd have to
regex apart.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TeamMember(BaseModel):
    name: str = Field(description="Full name of the leadership/team member.")
    role: Optional[str] = Field(
        default=None, description="Title or role, e.g. 'Co-Founder & CEO'."
    )
    linkedin_url: Optional[str] = Field(
        default=None,
        description="LinkedIn profile URL if present on the page or found via search fallback.",
    )
    source: str = Field(
        default="site",
        description="'site' if found on the crawled pages, 'search' if resolved via the search fallback.",
    )


class CompanyIntelligence(BaseModel):
    """The full structured record produced for one target domain."""

    domain: str = Field(description="The input domain this record was built for.")
    company_overview: str = Field(
        description="A concise 2-sentence summary of what the company does."
    )
    target_audience: str = Field(
        description="Who the product/service is built for, e.g. 'Developers building backend APIs'."
    )
    contact_points: list[str] = Field(
        default_factory=list,
        description="Generic/public emails found on the site (contact@, sales@, support@, etc.).",
    )
    leadership: list[TeamMember] = Field(
        default_factory=list,
        description="Key leadership/team members discoverable from the crawled pages.",
    )
    data_confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "0.0-1.0 estimate of how complete/reliable this record is, given what "
            "was actually retrievable from the site (fewer pages fetched, blocked "
            "pages, or missing fields should lower this)."
        ),
    )


class DomainRunResult(BaseModel):
    """Everything captured for one domain: the extraction plus run metadata.

    This is the unit that gets appended to the final output.json array —
    keeping the extraction and the operational metadata (errors, pages
    fetched, cost) in one record per domain rather than several parallel
    lists that would have to be zipped back together downstream.
    """

    intelligence: Optional[CompanyIntelligence] = None
    pages_fetched: list[str] = Field(default_factory=list)
    pages_failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
