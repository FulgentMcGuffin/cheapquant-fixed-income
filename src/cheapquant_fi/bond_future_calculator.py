"""Interface for bond future basis analytics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cheapquant_fi.bond_future_input import BondFutureInput
from cheapquant_fi.bond_future_output import BondFutureBasketOutput
from cheapquant_fi.bond_futures import BondFuture
from cheapquant_fi.instruments import Bond

if TYPE_CHECKING:
    from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext


@runtime_checkable
class BondFutureCalculator(Protocol):
    """Computes conversion factors and basis analytics for a delivery basket."""

    def compute_bond_future_analytics(
        self,
        request: BondFutureInput,
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",
    ) -> BondFutureBasketOutput:
        """Return per-bond basis analytics, cheapest to deliver first."""

    def compute_conversion_factor(self, bond: Bond, bond_future: BondFuture) -> float:
        """Return the conversion factor for delivering *bond* into the contract."""
