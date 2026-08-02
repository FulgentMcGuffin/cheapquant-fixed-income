"""QuantLib implementation of :class:`~cqfi.bond_future_calculator.BondFutureCalculator`.

Basis is measured to the last delivery day, the market convention for net
basis on contracts where the seller chooses the delivery date.  With
``P`` the clean price and ``A(x)`` accrued at ``x``, financing at rate ``r``
over the day-count fraction ``t(a, b)`` and coupons ``c_j`` paid in
``(settlement, delivery]``::

    dirty(D)    = (P + A(s)) * (1 + r*t(s, D)) - sum_j c_j * (1 + r*t(t_j, D))
    forward(D)  = dirty(D) - A(D)
    gross basis = P - F * CF
    net basis   = forward(D) - F * CF

The implied repo rate is the ``r`` that sets net basis to zero.
"""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from cqfi.bond_future_input import BondFutureInput
from cqfi.bond_future_output import BondFutureBasketOutput, BondFutureOutput
from cqfi.bond_futures import MONTHS_PER_YEAR, BondFuture
from cqfi.cache.decorators import cache_bond_future_analytics
from cqfi.date_utils import add_months, from_ql_date, to_ql_date
from cqfi.instruments import Bond
from cqfi.issuers import IssuerProfile
from cqfi.numeric_term_structure import NumericTermStructure
from cqfi.quantlib.quantlib_conversion_factor import conversion_factor
from cqfi.quantlib.quantlib_market_context import QuantlibMarketContext

_BASIS_POINT = 1e-4


class BondFutureAnalyticsError(ValueError):
    """Raised when basis analytics cannot be computed."""


class QuantLibBondFutureCalculator:
    """QuantLib-backed :class:`BondFutureCalculator`."""

    @cache_bond_future_analytics
    def compute_bond_future_analytics(
        self,
        request: BondFutureInput,
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",
    ) -> BondFutureBasketOutput:
        """Return per-bond basis analytics, cheapest to deliver first.

        Args:
            request: Basket, contract, trade date and optional futures price.
            market: Market context holding the discount curve.
            curve_label: Curve collection to discount with; the request's own
                label wins when it differs from the default.

        Returns:
            Analytics ordered by implied repo rate, highest first, so that
            ``index`` 0 and :meth:`BondFutureBasketOutput.ctd` agree.

        Raises:
            BondFutureAnalyticsError: If the basket is empty.
        """
        basket = request.delivery_basket
        if not basket.members:
            raise BondFutureAnalyticsError(
                f"Delivery basket for {request.bond_future} is empty"
            )

        label = (
            curve_label if request.curve_label == "BOND_ZERO" else request.curve_label
        )
        convention = request.bond_future.convention
        issuer = convention.issuer()
        settlement = request.settlement_date()
        delivery = request.delivery_date()

        saved_evaluation_date = ql.Settings.instance().evaluationDate
        try:
            ql.Settings.instance().evaluationDate = to_ql_date(settlement)
            curve_handle = market.curve_collection(label).bond_curve(issuer.source_code)
            # Reported on the basket output; the rate actually applied to a
            # given bond may differ when it carries its own override below.
            repo_rate = self._repo_rate(None, request, curve_handle, settlement, delivery)

            priced = [
                self._price_bond(
                    member.bond,
                    member.conversion_factor_override,
                    request,
                    issuer,
                    curve_handle,
                    settlement,
                    delivery,
                    self._repo_rate(
                        member.repo_term_structure_override,
                        request,
                        curve_handle,
                        settlement,
                        delivery,
                    ),
                )
                for member in basket.members
            ]
        finally:
            ql.Settings.instance().evaluationDate = saved_evaluation_date

        futures_price = request.futures_price
        is_implied = futures_price is None
        if is_implied:
            futures_price = min(item["implied_fair_futures_price"] for item in priced)

        outputs = self._rank(priced, futures_price, settlement, delivery, convention)
        return BondFutureBasketOutput(
            bond_future=request.bond_future,
            trade_date=request.trade_date,
            settlement_date=settlement,
            delivery_date=delivery,
            futures_price=futures_price,
            futures_price_is_implied=is_implied,
            repo_rate=repo_rate * 100.0,
            outputs=outputs,
        )

    def compute_conversion_factor(self, bond: Bond, bond_future: BondFuture) -> float:
        """Return the conversion factor for delivering *bond* into the contract."""
        return conversion_factor(bond, bond_future)

    # ------------------------------------------------------------------ #
    # Pricing
    # ------------------------------------------------------------------ #
    def _price_bond(
        self,
        bond: Bond,
        override: float | None,
        request: BondFutureInput,
        issuer: IssuerProfile,
        curve_handle: ql.YieldTermStructureHandle,
        settlement: date,
        delivery: date,
        repo_rate: float,
    ) -> dict[str, object]:
        """Price one deliverable bond spot and forward to delivery."""
        qlbond = build_deliverable(bond, issuer, settlement)
        qlbond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))

        ql_settlement = to_ql_date(settlement)
        clean_price = qlbond.cleanPrice()
        accrued = qlbond.accruedAmount(ql_settlement)

        coupons = self._coupons_between(qlbond, ql_settlement, to_ql_date(delivery))
        accrued_at_delivery = qlbond.accruedAmount(to_ql_date(delivery))

        day_count = request.bond_future.convention.repo_day_count()
        forward_clean = self._forward_clean_price(
            clean_price + accrued,
            coupons,
            accrued_at_delivery,
            settlement,
            delivery,
            repo_rate,
            day_count,
        )
        factor = (
            override
            if override is not None
            else conversion_factor(bond, request.bond_future)
        )
        if factor <= 0:
            raise BondFutureAnalyticsError(
                f"Conversion factor for {bond.user_friendly_id or bond.bond_id} "
                f"must be positive, got {factor}"
            )

        delta, gamma = self._delta_gamma(qlbond, curve_handle, issuer, request.shift_bp)
        return {
            "bond": bond,
            "conversion_factor": factor,
            "clean_price": clean_price,
            "accrued_interest": accrued,
            "accrued_at_delivery": accrued_at_delivery,
            "coupons": coupons,
            "forward_clean_price": forward_clean,
            "implied_fair_futures_price": forward_clean / factor,
            "delta": delta,
            "gamma": gamma,
        }

    def _coupons_between(
        self, qlbond: ql.Bond, start: ql.Date, end: ql.Date
    ) -> list[tuple[date, float]]:
        """Return coupons paid in ``(start, end]`` as ``(date, amount)`` pairs."""
        return [
            (from_ql_date(cashflow.date()), cashflow.amount())
            for cashflow in qlbond.cashflows()
            if start < cashflow.date() <= end and ql.as_coupon(cashflow) is not None
        ]

    def _forward_clean_price(
        self,
        dirty_price: float,
        coupons: list[tuple[date, float]],
        accrued_at_delivery: float,
        settlement: date,
        delivery: date,
        repo_rate: float,
        day_count: ql.DayCounter,
    ) -> float:
        """Carry a bond's dirty price to delivery and strip accrued back off."""
        grown = dirty_price * (
            1.0 + repo_rate * _year_fraction(day_count, settlement, delivery)
        )
        reinvested = sum(
            amount * (1.0 + repo_rate * _year_fraction(day_count, paid_on, delivery))
            for paid_on, amount in coupons
        )
        return grown - reinvested - accrued_at_delivery

    def _implied_repo_rate(
        self,
        dirty_price: float,
        coupons: list[tuple[date, float]],
        invoice_price: float,
        settlement: date,
        delivery: date,
        day_count: ql.DayCounter,
    ) -> float | None:
        """Return the financing rate that sets net basis to zero, in percent.

        Returns ``None`` when the financing term is degenerate — a coupon
        large enough to dominate the weighted term drives the denominator to
        zero or below.
        """
        denominator = dirty_price * _year_fraction(
            day_count, settlement, delivery
        ) - sum(
            amount * _year_fraction(day_count, paid_on, delivery)
            for paid_on, amount in coupons
        )
        if denominator <= 0:
            return None
        numerator = invoice_price + sum(amount for _, amount in coupons) - dirty_price
        return numerator / denominator * 100.0

    def _delta_gamma(
        self,
        qlbond: ql.Bond,
        curve_handle: ql.YieldTermStructureHandle,
        issuer: IssuerProfile,
        shift_bp: float,
    ) -> tuple[float, float]:
        """Return clean-price delta and gamma to a parallel zero-curve shift.

        Uses a quote-linked :class:`ql.ZeroSpreadedTermStructure` so each bump
        recalculates without rebuilding the curve.  Delta is per basis point
        and gamma is per basis point squared.
        """
        quote = ql.SimpleQuote(0.0)
        shifted = ql.YieldTermStructureHandle(
            ql.ZeroSpreadedTermStructure(
                curve_handle,
                ql.QuoteHandle(quote),
                ql.Compounded,
                issuer.frequency,
                issuer.day_count,
            )
        )
        qlbond.setPricingEngine(ql.DiscountingBondEngine(shifted))
        try:
            step = shift_bp * _BASIS_POINT
            quote.setValue(step)
            up = qlbond.cleanPrice()
            quote.setValue(-step)
            down = qlbond.cleanPrice()
            quote.setValue(0.0)
            base = qlbond.cleanPrice()
        finally:
            qlbond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))

        delta = (up - down) / (2.0 * shift_bp)
        gamma = (up - 2.0 * base + down) / (shift_bp**2)
        return delta, gamma

    # ------------------------------------------------------------------ #
    # Financing and ranking
    # ------------------------------------------------------------------ #
    def _repo_rate(
        self,
        term_structure: NumericTermStructure | None,
        request: BondFutureInput,
        curve_handle: ql.YieldTermStructureHandle,
        settlement: date,
        delivery: date,
    ) -> float:
        """Return the financing rate to delivery, as a decimal.

        Args:
            term_structure: A per-bond override, taking precedence over the
                request's basket-wide term structure when supplied.

        Falls back to the discount curve's own simple forward rate when
        neither a per-bond nor a basket-wide repo term structure is supplied.
        """
        convention = request.bond_future.convention
        effective = (
            term_structure if term_structure is not None else request.repo_term_structure
        )
        if effective is not None:
            return effective.rate_for(
                delivery,
                settlement_days=convention.repo_settlement_days(),
                issuer=convention.issuer(),
            )
        if settlement >= delivery:
            return 0.0
        return curve_handle.forwardRate(
            to_ql_date(settlement),
            to_ql_date(delivery),
            convention.repo_day_count(),
            ql.Simple,
        ).rate()

    def _rank(
        self,
        priced: list[dict[str, object]],
        futures_price: float,
        settlement: date,
        delivery: date,
        convention,
    ) -> tuple[BondFutureOutput, ...]:
        """Build outputs ranked by implied repo rate, cheapest to deliver first."""
        day_count = convention.repo_day_count()
        rows = []
        for item in priced:
            factor = item["conversion_factor"]
            invoice = futures_price * factor + item["accrued_at_delivery"]
            implied_repo = self._implied_repo_rate(
                item["clean_price"] + item["accrued_interest"],
                item["coupons"],
                invoice,
                settlement,
                delivery,
                day_count,
            )
            rows.append(
                {
                    **item,
                    "implied_repo_rate": implied_repo,
                    "gross_basis": item["clean_price"] - futures_price * factor,
                    "net_basis": item["forward_clean_price"] - futures_price * factor,
                }
            )

        # Highest implied repo is cheapest to deliver, so it takes index 0.
        # Bonds with a degenerate rate sort last, then by net basis.
        rows.sort(
            key=lambda row: (
                row["implied_repo_rate"] is None,
                -(row["implied_repo_rate"] or 0.0),
                row["net_basis"],
            )
        )
        return tuple(
            BondFutureOutput(
                bond=row["bond"],
                conversion_factor=row["conversion_factor"],
                clean_price=row["clean_price"],
                accrued_interest=row["accrued_interest"],
                forward_clean_price=row["forward_clean_price"],
                implied_repo_rate=row["implied_repo_rate"],
                gross_basis=row["gross_basis"],
                net_basis=row["net_basis"],
                index=index,
                delta=row["delta"],
                gamma=row["gamma"],
                implied_fair_futures_price=row["implied_fair_futures_price"],
            )
            for index, row in enumerate(rows)
        )


def build_deliverable(
    bond: Bond, issuer: IssuerProfile, settlement: date | None = None
) -> ql.FixedRateBond:
    """Build a QuantLib bond for pricing, honouring an irregular first coupon.

    Unlike the schedule used for conversion factors, this reflects the bond's
    true first coupon date so that accrued interest and cashflow dates are
    right for fresh issues with a long or short first period.

    Args:
        bond: The bond to build.
        issuer: Conventions to build it under.
        settlement: Date the schedule must already have started by.  Used only
            when the bond's issue date is missing or later than settlement.

    Returns:
        A fixed-rate bond whose schedule covers *settlement* through maturity.
    """
    start = _schedule_start(bond, issuer, settlement)
    issue = to_ql_date(start)
    maturity = to_ql_date(bond.maturity)
    convention = issuer.payment_convention
    # A recorded first coupon only describes the true schedule; it is
    # meaningless once the start date has been synthesised.
    first_coupon = (
        to_ql_date(bond.first_coupon_date)
        if bond.first_coupon_date is not None and start == bond.issue_date
        else ql.Date()
    )

    schedule = ql.Schedule(
        issue,
        maturity,
        ql.Period(issuer.frequency),
        issuer.calendar(),
        convention,
        convention,
        ql.DateGeneration.Backward,
        False,
        first_coupon,
    )
    return issuer.make_QL_fixed_rate_bond(
        schedule,
        [0.0 if bond.coupon is None else bond.coupon / 100.0],
        issue_date=issue,
    )


def _schedule_start(bond: Bond, issuer: IssuerProfile, settlement: date | None) -> date:
    """Return a schedule start date at or before *settlement*.

    ``bond_universe`` rows may carry no issue date, so fall back to stepping
    whole coupon periods back from maturity — the regular schedule the bond
    would have had.
    """
    anchor = settlement or bond.maturity
    if bond.issue_date is not None and bond.issue_date <= anchor:
        return bond.issue_date

    step = MONTHS_PER_YEAR // issuer.frequency
    start = bond.maturity
    while start > anchor:
        start = add_months(start, -step)
    return start


def _year_fraction(day_count: ql.DayCounter, start: date, end: date) -> float:
    return day_count.yearFraction(to_ql_date(start), to_ql_date(end))
