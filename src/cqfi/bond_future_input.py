"""Input request type for bond future basis analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cqfi.bond_futures import BondFuture
from cqfi.delivery_basket import DeliveryBasket
from cqfi.numeric_term_structure import NumericTermStructure


@dataclass(frozen=True)
class BondFutureInput:
    """Inputs required to compute basis analytics for a delivery basket.

    Attributes:
        delivery_basket: The deliverable bonds, whose membership was fixed by
            the contract's delivery month.
        bond_future: The dated contract being analysed.
        trade_date: Date the analytics are computed as of.  It need not be
            related to the delivery month — a basket's membership is fixed
            once, but it can be valued on any earlier date.
        repo_term_structure: Financing curve applied to every deliverable
            bond, unless a member of ``delivery_basket`` carries its own via
            ``DeliveryBasket.add(..., repo_term_structure=...)`` or
            ``DeliveryBasket.set_repo_term_structure``, in which case the
            per-bond curve wins for that bond only. When neither is set, the
            bond curve's own forward rate to delivery is used instead.
        curve_label: Which curve collection to discount with.
        futures_price: Observed futures price.  When ``None`` the price is
            implied so that the cheapest-to-deliver bond's net basis is zero.
        shift_bp: Size of the parallel zero-curve shift used for delta and
            gamma, in basis points.
    """

    delivery_basket: DeliveryBasket
    bond_future: BondFuture
    trade_date: date
    repo_term_structure: NumericTermStructure | None = None
    curve_label: str = "BOND_ZERO"
    futures_price: float | None = None
    shift_bp: float = 1.0

    def __post_init__(self) -> None:
        if self.shift_bp <= 0:
            raise ValueError(f"shift_bp must be positive, got {self.shift_bp}")
        if self.futures_price is not None and self.futures_price <= 0:
            raise ValueError(
                f"futures_price must be positive, got {self.futures_price}"
            )
        if self.trade_date > self.delivery_date():
            raise ValueError(
                f"trade_date {self.trade_date.isoformat()} is after the delivery "
                f"date {self.delivery_date().isoformat()}"
            )

    @classmethod
    def from_basket(
        cls,
        basket: DeliveryBasket,
        trade_date: date,
        **kwargs,
    ) -> BondFutureInput:
        """Build a request from a basket, taking its contract along."""
        return cls(
            delivery_basket=basket,
            bond_future=basket.bond_future,
            trade_date=trade_date,
            **kwargs,
        )

    def settlement_date(self) -> date:
        """Return the bond settlement date implied by the trade date."""
        return self.bond_future.convention.issuer().settlement_date(self.trade_date)

    def delivery_date(self) -> date:
        """Return the last delivery day, which basis is measured to."""
        return self.bond_future.delivery_end_date()
