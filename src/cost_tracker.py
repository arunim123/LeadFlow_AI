"""Token usage / cost estimation and a small run summary table.

Pricing is approximate list pricing per the values in `Settings` — good
enough for relative cost-per-domain comparisons, not for finance-grade
billing reconciliation (check current published pricing for that).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import settings


@dataclass
class DomainCost:
    domain: str
    input_tokens: int
    output_tokens: int

    @property
    def estimated_usd(self) -> float:
        return (
            self.input_tokens / 1_000_000 * settings.price_per_million_input
            + self.output_tokens / 1_000_000 * settings.price_per_million_output
        )


def summarize(costs: list[DomainCost]) -> str:
    lines = ["domain".ljust(20) + "in_tok".rjust(8) + "out_tok".rjust(9) + "  est. cost"]
    lines.append("-" * 50)
    total = 0.0
    for c in costs:
        total += c.estimated_usd
        lines.append(
            f"{c.domain[:20]:<20}{c.input_tokens:>8}{c.output_tokens:>9}   ${c.estimated_usd:.4f}"
        )
    lines.append("-" * 50)
    lines.append(f"{'TOTAL':<20}{'':>8}{'':>9}   ${total:.4f}")
    return "\n".join(lines)
