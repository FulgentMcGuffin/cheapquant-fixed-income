"""Analytics calculator interface for bonds and CMTs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cqfi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cqfi.analytics_output import FixedIncomeAnalyticsOutput

if TYPE_CHECKING:
    from cqfi.quantlib.quantlib_market_context import QuantlibMarketContext


@runtime_checkable
class AnalyticsCalculator(Protocol):
    """Interface for computing :class:`FixedIncomeAnalyticsOutput` on bonds and CMTs."""

    def compute_bond_analytics(
        self,
        request: BondAnalyticsInput,
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",
    ) -> tuple[FixedIncomeAnalyticsOutput, FixedIncomeAnalyticsOutput | None, FixedIncomeAnalyticsOutput | None]:
        """Return bond analytics and optional maturity-matched CMT analytics and optional fixed-coupon CMT analytics."""
        
    def compute_cmt_analytics(
        self,
        request: CmtAnalyticsInput,
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",        
    ) -> FixedIncomeAnalyticsOutput:
        """Return CMT analytics (from Tenor String and Curve)"""
        

