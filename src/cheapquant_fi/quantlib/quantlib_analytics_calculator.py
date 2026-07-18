"""QuantLib implementation of :class:`~cheapquant_fi.analytics_calculator.AnalyticsCalculator`."""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from cheapquant_fi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cheapquant_fi.analytics_output import FixedIncomeAnalyticsOutput
from cheapquant_fi.issuers import IssuerProfile, resolve_issuer
from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext
from cheapquant_fi.tenors import TENOR_COLUMN_TO_YEARS, label_to_column

_INPUT_PRICE_FIELDS = frozenset({"clean_price", "yield_to_maturity"})


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _tenor_label_to_years(label: str) -> float:
    column = label_to_column(label)
    if column is None:
        raise ValueError(f"Unknown tenor label: {label!r}")
    return TENOR_COLUMN_TO_YEARS[column]


class QuantLibAnalyticsCalculator:
    """QuantLib-backed :class:`AnalyticsCalculator`."""

    def compute_bond_analytics(
        self,
        request: BondAnalyticsInput,
        market: QuantlibMarketContext,
        *,
        curve_label: str = "default",
    ) -> FixedIncomeAnalyticsOutput:
        issuer = resolve_issuer(request.issuer)
        settlement = _to_ql_date(request.settlement_date)
        ql.Settings.instance().evaluationDate = settlement

        qlbond = self._build_fixed_rate_bond(issuer, request)
        curve_handle = self._bond_curve(market, issuer.source_code, curve_label)
        use_curve = self._uses_curve(request)

        if use_curve:
            qlbond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))
            metrics = self._bond_metrics_from_priced_bond(
                qlbond, issuer, settlement, curve_handle=curve_handle
            )
        else:
            metrics = self._bond_metrics_from_input(
                qlbond, issuer, settlement, request.input_column, request.input_value
            )

        return metrics

    def compute_cmt_analytics(
        self,
        request: CmtAnalyticsInput,
        market: QuantlibMarketContext,
        *,
        curve_label: str = "default",
    ) -> FixedIncomeAnalyticsOutput:
        issuer = resolve_issuer(request.issuer)
        settlement = _to_ql_date(request.settlement_date)
        ql.Settings.instance().evaluationDate = settlement

        tenor_years = _tenor_label_to_years(request.tenor_label)
        bond = self._build_cmt(issuer, settlement, tenor_years, request.coupon)
        has_coupon = request.coupon is not None
        curve_handle = self._bond_curve(market, issuer.source_code, curve_label)
        use_curve = self._uses_curve(request)

        if use_curve:
            bond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))
            return self._bond_metrics_from_priced_bond(
                bond,
                issuer,
                settlement,
                curve_handle=curve_handle,
                include_accrued=has_coupon,
            )

        return self._bond_metrics_from_input(
            bond,
            issuer,
            settlement,
            request.input_column,
            request.input_value,
            include_accrued=has_coupon,
        )

    def _uses_curve(self, request: BondAnalyticsInput | CmtAnalyticsInput) -> bool:
        if request.input_column is None:
            return True
        if request.input_value is None:
            raise ValueError(
                f"input_value is required when input_column={request.input_column!r}"
            )
        return False

    def _bond_curve(
        self,
        market: QuantlibMarketContext,
        issuer_code: str,
        curve_label: str,
    ) -> ql.YieldTermStructureHandle:
        return market.curve_collection(curve_label).bond_curve(issuer_code)

    def _build_fixed_rate_bond(
        self,
        issuer: IssuerProfile,
        request: BondAnalyticsInput,
    ) -> ql.FixedRateBond:
        calendar = issuer.calendar()
        issue = _to_ql_date(request.issue_date or request.settlement_date)
        maturity = _to_ql_date(request.maturity_date)
        settlement = _to_ql_date(request.settlement_date)

        schedule = ql.Schedule(
            issue,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            ql.ModifiedFollowing,
            ql.ModifiedFollowing,
            ql.DateGeneration.Backward,
            False,
        )
        return issuer.make_QL_fixed_rate_bond(
            schedule,
            [request.coupon / 100.0],
            redemption=request.face_amount,
            issue_date=issue,
        )

    def _cmt_maturity(
        self,
        issuer: IssuerProfile,
        settlement: ql.Date,
        tenor_years: float,
    ) -> ql.Date:
        calendar = issuer.calendar()
        if tenor_years < 1.0:
            days = int(round(tenor_years * 365))
            return calendar.advance(
                settlement, days, ql.Days, ql.ModifiedFollowing
            )
        return calendar.advance(
            settlement,
            ql.Period(int(round(tenor_years * 12)), ql.Months),
            ql.ModifiedFollowing,
        )

    def _build_cmt(
        self,
        issuer: IssuerProfile,
        settlement: ql.Date,
        tenor_years: float,
        coupon: float | None,
    ) -> ql.Bond:
        """Build a zero-coupon or fixed-coupon synthetic CMT."""
        calendar = issuer.calendar()
        maturity = self._cmt_maturity(issuer, settlement, tenor_years)

        if coupon is None:
            return ql.ZeroCouponBond(
                issuer.settlement_days,
                calendar,
                100.0,
                maturity,
                ql.ModifiedFollowing,
                100.0,
                settlement,
            )

        schedule = ql.Schedule(
            settlement,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            ql.ModifiedFollowing,
            ql.ModifiedFollowing,
            ql.DateGeneration.Backward,
            False,
        )
        return issuer.make_QL_fixed_rate_bond(
            schedule,
            [coupon / 100.0],
            issue_date=settlement,
        )

    def _bond_metrics_from_priced_bond(
        self,
        bond: ql.Bond,
        issuer: IssuerProfile,
        settlement: ql.Date,
        *,
        curve_handle: ql.YieldTermStructureHandle | None = None,
        include_accrued: bool = True,
    ) -> FixedIncomeAnalyticsOutput:
        clean = bond.cleanPrice()
        dirty = bond.dirtyPrice()
        accrued = (dirty - clean) if include_accrued else 0.0
        yld = bond.bondYield(
            issuer.day_count,
            ql.Compounded,
            issuer.frequency,
        )

        macaulay = ql.BondFunctions.duration(
            bond,
            yld,
            issuer.day_count,
            ql.Compounded,
            issuer.frequency,
            ql.Duration.Macaulay,
            settlement,
        )
        convexity = ql.BondFunctions.convexity(
            bond,
            yld,
            issuer.day_count,
            ql.Compounded,
            issuer.frequency,
            settlement,
        )
        bpv = ql.BondFunctions.basisPointValue(
            bond,
            yld,
            issuer.day_count,
            ql.Compounded,
            issuer.frequency,
            settlement,
        )

        z_spread_bps = None
        if curve_handle is not None:
            z_spread_bps = (
                ql.BondFunctions.zSpread(
                    bond,
                    ql.BondPrice(clean, ql.BondPrice.Clean),
                    curve_handle.currentLink(),
                    ql.Compounded,
                    issuer.frequency,
                )
                * 10_000.0
            )

        return FixedIncomeAnalyticsOutput(
            yield_to_maturity=yld * 100.0,
            clean_price=clean,
            dirty_price=dirty,
            accrued_interest=accrued if include_accrued else None,
            duration=macaulay,
            convexity=convexity,
            dv01_sensitivity=bpv,
            gamma_sensitivity=convexity * clean / 100.0 if convexity is not None else None,
            z_spread=z_spread_bps,
        )

    def _bond_metrics_from_input(
        self,
        bond: ql.Bond,
        issuer: IssuerProfile,
        settlement: ql.Date,
        input_column: str | None,
        input_value: float | None,
        *,
        include_accrued: bool = True,
    ) -> FixedIncomeAnalyticsOutput:
        if input_column not in _INPUT_PRICE_FIELDS:
            raise ValueError(
                f"Unsupported input_column {input_column!r}; "
                f"expected one of {sorted(_INPUT_PRICE_FIELDS)}"
            )
        assert input_value is not None

        dc = issuer.day_count
        freq = issuer.frequency
        comp = ql.Compounded

        if input_column == "yield_to_maturity":
            yld = input_value / 100.0
            clean = ql.BondFunctions.cleanPrice(
                bond, yld, dc, comp, freq, settlement
            )
        else:
            clean = input_value
            yld = ql.BondFunctions.bondYield(
                bond, clean, dc, comp, freq, settlement
            )

        dirty = ql.BondFunctions.dirtyPrice(
            bond, yld, dc, comp, freq, settlement
        )
        accrued = dirty - clean if include_accrued else 0.0

        macaulay = ql.BondFunctions.duration(
            bond, yld, dc, comp, freq, ql.Duration.Macaulay, settlement
        )
        convexity = ql.BondFunctions.convexity(
            bond, yld, dc, comp, freq, settlement
        )
        bpv = ql.BondFunctions.basisPointValue(
            bond, yld, dc, comp, freq, settlement
        )

        return FixedIncomeAnalyticsOutput(
            yield_to_maturity=yld * 100.0,
            clean_price=clean,
            dirty_price=dirty,
            accrued_interest=accrued if include_accrued else None,
            duration=macaulay,
            convexity=convexity,
            dv01_sensitivity=bpv,
            gamma_sensitivity=convexity * clean / 100.0 if convexity is not None else None,
        )
