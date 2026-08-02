"""Output types for bond future basis analytics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import polars as pl

from cqfi.bond_futures import BondFuture
from cqfi.instruments import Bond

# Analytics columns, in the order the specification asks for them.  The
# deliverable bond's maturity and coupon lead, then the basis measures.
BOND_FUTURE_OUTPUT_COLUMNS: tuple[str, ...] = (
    "maturity",
    "coupon",
    "conversion_factor",
    "implied_repo_rate",
    "net_basis",
    "gross_basis",
    "index",
    "delta",
    "gamma",
    "implied_fair_futures_price",
)


@dataclass(frozen=True)
class BondFutureOutput:
    """Basis analytics for one deliverable bond.

    Prices are per 100 nominal.  ``implied_repo_rate`` is in percent, and
    ``delta`` / ``gamma`` are clean-price sensitivities to a parallel shift of
    the zero curve, expressed per basis point and per basis point squared.

    Attributes:
        bond: The deliverable bond.
        conversion_factor: Supplied verbatim when the basket carried an
            override, otherwise computed from the contract terms.
        clean_price: Curve-implied clean price on the settlement date.
        accrued_interest: Accrued interest on the settlement date.
        forward_clean_price: Clean price forward to the delivery date.
        implied_repo_rate: Return from buying the bond and delivering it,
            or ``None`` when the financing term is degenerate.
        gross_basis: ``clean_price - futures_price * conversion_factor``.
        net_basis: Gross basis less carry to delivery.
        index: Rank by implied repo rate, ``0`` being the cheapest to deliver.
        delta: Clean-price change per basis point of parallel zero shift.
        gamma: Change in *delta* per basis point.
        implied_fair_futures_price: ``forward_clean_price / conversion_factor``.
    """

    bond: Bond
    conversion_factor: float
    clean_price: float
    accrued_interest: float
    forward_clean_price: float
    implied_repo_rate: float | None
    gross_basis: float
    net_basis: float
    index: int
    delta: float
    gamma: float
    implied_fair_futures_price: float

    def as_dict(self) -> dict[str, object]:
        """Return the analytics alongside the underlying bond."""
        return {
            "bond": self.bond.as_dict(),
            "index": self.index,
            "conversion_factor": self.conversion_factor,
            "clean_price": self.clean_price,
            "accrued_interest": self.accrued_interest,
            "forward_clean_price": self.forward_clean_price,
            "implied_repo_rate": self.implied_repo_rate,
            "net_basis": self.net_basis,
            "gross_basis": self.gross_basis,
            "delta": self.delta,
            "gamma": self.gamma,
            "implied_fair_futures_price": self.implied_fair_futures_price,
        }

    def as_json(self, **kwargs) -> str:
        """Return the analytics as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)


@dataclass(frozen=True)
class BondFutureBasketOutput:
    """Basis analytics for a whole delivery basket, cheapest to deliver first.

    Attributes:
        bond_future: The contract analysed.
        trade_date: Date the analytics were computed as of.
        settlement_date: Bond settlement implied by the trade date.
        delivery_date: Last delivery day, which basis is measured to.
        futures_price: The price basis was measured against.
        futures_price_is_implied: Whether that price was implied from the
            basket rather than observed.
        repo_rate: Basket-wide financing rate used to carry bonds to
            delivery, in percent — the rate implied by
            ``BondFutureInput.repo_term_structure``, or the discount curve's
            own forward rate when that is unset. A deliverable bond with its
            own ``DeliveryBasket`` repo term structure override is actually
            carried at that rate instead, so its analytics may not match this
            value; see ``DeliveryBasket.repo_term_structure_for``.
        outputs: Per-bond analytics, ordered by ``index``.
    """

    bond_future: BondFuture
    trade_date: date
    settlement_date: date
    delivery_date: date
    futures_price: float
    futures_price_is_implied: bool
    repo_rate: float
    outputs: tuple[BondFutureOutput, ...]

    def ctd(self) -> BondFutureOutput:
        """Return the cheapest-to-deliver bond's analytics.

        Raises:
            ValueError: If the basket is empty.
        """
        if not self.outputs:
            raise ValueError(
                f"{self.bond_future} has an empty delivery basket; "
                "there is no cheapest-to-deliver bond"
            )
        return self.outputs[0]

    def to_polars(self) -> pl.DataFrame:
        """Return one row per deliverable bond, cheapest to deliver first."""
        return pl.DataFrame(
            [
                {
                    "maturity": output.bond.maturity,
                    "coupon": output.bond.coupon,
                    "conversion_factor": output.conversion_factor,
                    "implied_repo_rate": output.implied_repo_rate,
                    "net_basis": output.net_basis,
                    "gross_basis": output.gross_basis,
                    "index": output.index,
                    "delta": output.delta,
                    "gamma": output.gamma,
                    "implied_fair_futures_price": output.implied_fair_futures_price,
                }
                for output in self.outputs
            ],
            schema={
                "maturity": pl.Date,
                "coupon": pl.Float64,
                "conversion_factor": pl.Float64,
                "implied_repo_rate": pl.Float64,
                "net_basis": pl.Float64,
                "gross_basis": pl.Float64,
                "index": pl.Int64,
                "delta": pl.Float64,
                "gamma": pl.Float64,
                "implied_fair_futures_price": pl.Float64,
            },
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view, bonds ordered cheapest to deliver first."""
        return {
            "contract": str(self.bond_future),
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "delivery_date": self.delivery_date.isoformat(),
            "futures_price": self.futures_price,
            "futures_price_is_implied": self.futures_price_is_implied,
            "repo_rate": self.repo_rate,
            "bond_count": len(self.outputs),
            "analytics": [output.as_dict() for output in self.outputs],
        }

    def as_json(self, **kwargs) -> str:
        """Return the basket analytics as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)

    def __len__(self) -> int:
        return len(self.outputs)
