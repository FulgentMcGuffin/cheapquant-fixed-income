"""Optional fixed-income analytics measures for bonds and CMTs."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields

# Metric column names from semantics/bond_analytics.yaml (bond_analytics, lines 106-136).
ANALYTICS_METRIC_FIELDS: tuple[str, ...] = (
    "yield_to_maturity",
    "clean_price",
    "dirty_price",
    "accrued_interest",
    "duration",
    "convexity",
    "dv01_sensitivity",
    "gamma_sensitivity",
    "mm_cmt_yield",
    "mm_fc_cmt_yield",
    "z_spread",
    "repo_rate_1m",
    "repo_rate_3m",
    "repo_rate_6m",
    "repo_rate_1y",
    "carry_1m",
    "carry_3m",
    "carry_6m",
    "carry_1y",
    "roll_1m",
    "roll_3m",
    "roll_6m",
    "roll_1y",
    "carry_roll_1m",
    "carry_roll_3m",
    "carry_roll_6m",
    "carry_roll_1y",
    "asw_spread_1m",
    "asw_spread_3m",
    "asw_spread_6m",
    "asw_spread_1y",
)


@dataclass
class FixedIncomeAnalyticsOutput:
    """Computed analytics for a bond or CMT.

    Field names match the numeric metric columns in ``bond_analytics`` /
    ``cmt_analytics`` (``semantics/bond_analytics.yaml``).  Every measure is
    optional because a given run may only populate a subset; unset fields are
    ``None``.

    Percent fields (yield, price, duration, carry, roll, etc.) are expressed
    in percent (e.g. ``4.25`` means 4.25%).  ``z_spread`` is in basis points.
    """

    yield_to_maturity: float | None = None
    clean_price: float | None = None
    dirty_price: float | None = None
    accrued_interest: float | None = None
    duration: float | None = None
    convexity: float | None = None
    dv01_sensitivity: float | None = None
    gamma_sensitivity: float | None = None
    mm_cmt_yield: float | None = None
    mm_fc_cmt_yield: float | None = None
    z_spread: float | None = None
    repo_rate_1m: float | None = None
    repo_rate_3m: float | None = None
    repo_rate_6m: float | None = None
    repo_rate_1y: float | None = None
    carry_1m: float | None = None
    carry_3m: float | None = None
    carry_6m: float | None = None
    carry_1y: float | None = None
    roll_1m: float | None = None
    roll_3m: float | None = None
    roll_6m: float | None = None
    roll_1y: float | None = None
    carry_roll_1m: float | None = None
    carry_roll_3m: float | None = None
    carry_roll_6m: float | None = None
    carry_roll_1y: float | None = None
    asw_spread_1m: float | None = None
    asw_spread_3m: float | None = None
    asw_spread_6m: float | None = None
    asw_spread_1y: float | None = None

    def as_dict(self) -> dict[str, float]:
        """Return populated metrics only (non-``None`` values)."""
        return {
            field.name: value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None
        }

    def as_json(self, **kwargs) -> str:
        """Return populated metrics as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)
