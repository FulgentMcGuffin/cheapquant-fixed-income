"""QuantLib implementation of :class:`~cheapquant_fi.analytics_calculator.AnalyticsCalculator`."""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from cheapquant_fi.analytics_input import BondAnalyticsInput, CmtAnalyticsInput
from cheapquant_fi.analytics_output import FixedIncomeAnalyticsOutput
from cheapquant_fi.issuers import IssuerProfile, resolve_issuer
from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext
from cheapquant_fi.numeric_term_structure import NumericTermStructure
from cheapquant_fi.tenor import Tenor
from cheapquant_fi.ycs_tenors import TENOR_COLUMN_TO_YEARS, label_to_column

_INPUT_PRICE_FIELDS = frozenset({"clean_price", "yield_to_maturity"})
_ROLL_TENORS: tuple[tuple[str, Tenor], ...] = (
    ("1m", Tenor.parse("1m")),
    ("3m", Tenor.parse("3m")),
    ("6m", Tenor.parse("6m")),
    ("1y", Tenor.parse("1y")),
)


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


def _bond_settlement(issuer: IssuerProfile, settlement: ql.Date) -> ql.Date:
    calendar = issuer.calendar()
    result = settlement
    for _ in range(issuer.settlement_days):
        result = calendar.advance(result, 1, ql.Days, ql.Following)
    return result


def _subtract_tenor(ql_date: ql.Date, tenor: Tenor, issuer: IssuerProfile) -> ql.Date:
    """Move *ql_date* earlier by *tenor* on the issuer calendar."""
    simplified = tenor.simplify()
    calendar = issuer.calendar()
    convention = ql.ModifiedFollowing
    result = ql_date
    if simplified.days:
        result = result - simplified.days
    if simplified.weeks:
        result = calendar.advance(
            result,
            ql.Period(-simplified.weeks, ql.Weeks),
            convention,
            True,
        )
    if simplified.months:
        result = calendar.advance(
            result,
            ql.Period(-simplified.months, ql.Months),
            convention,
            True,
        )
    if simplified.years:
        result = calendar.advance(
            result,
            ql.Period(-simplified.years, ql.Years),
            convention,
            True,
        )
    return result


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
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",
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
                qlbond,
                issuer,
                settlement,
                curve_handle=curve_handle,
                issue_date=_to_ql_date(request.issue_date or request.settlement_date),
                maturity_date=_to_ql_date(request.maturity_date),
                coupon_pct=request.coupon,
                face_amount=request.face_amount,
                repo_term_structure=request.repo_term_structure,
            )
        else:
            metrics = self._bond_metrics_from_input(
                qlbond, issuer, settlement, request.input_column, request.input_value
            )

        return metrics

    def compute_cmt_analytics(
        self,
        request: CmtAnalyticsInput,
        market: QuantlibMarketContext = None,
        *,
        curve_label: str = "BOND_ZERO",
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
                issue_date=settlement,
                maturity_date=bond.maturityDate(),
                coupon_pct=request.coupon,
                face_amount=100.0,
                zero_coupon=not has_coupon,
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

    def _build_bond_for_roll(
        self,
        issuer: IssuerProfile,
        issue: ql.Date,
        maturity: ql.Date,
        settlement: ql.Date,
        *,
        coupon_pct: float | None,
        face_amount: float,
        zero_coupon: bool,
    ) -> ql.Bond:
        calendar = issuer.calendar()
        if zero_coupon or coupon_pct is None:
            return ql.ZeroCouponBond(
                issuer.settlement_days,
                calendar,
                face_amount,
                maturity,
                ql.ModifiedFollowing,
                face_amount,
                settlement,
            )

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
            [coupon_pct / 100.0],
            redemption=face_amount,
            issue_date=issue,
        )

    def _bond_yield_on_curve(
        self,
        bond: ql.Bond,
        issuer: IssuerProfile,
        curve_handle: ql.YieldTermStructureHandle,
        *,
        evaluation_date: ql.Date | None = None,
    ) -> float:
        saved = ql.Settings.instance().evaluationDate
        if evaluation_date is not None:
            ql.Settings.instance().evaluationDate = evaluation_date
        try:
            bond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))
            return (
                bond.bondYield(
                    issuer.day_count,
                    ql.Compounded,
                    issuer.frequency,
                )
                * 100.0
            )
        finally:
            ql.Settings.instance().evaluationDate = saved

    def _compute_yield_rolls(
        self,
        spot_yld_pct: float,
        issuer: IssuerProfile,
        settlement: ql.Date,
        issue: ql.Date,
        maturity: ql.Date,
        curve_handle: ql.YieldTermStructureHandle,
        *,
        coupon_pct: float | None,
        face_amount: float,
        zero_coupon: bool,
    ) -> dict[str, float | None]:
        rolls: dict[str, float | None] = {}
        for label, tenor in _ROLL_TENORS:
            earlier_maturity = _subtract_tenor(maturity, tenor, issuer)
            if earlier_maturity <= settlement:
                rolls[f"roll_{label}_spotyield"] = None
            else:
                rolled_bond = self._build_bond_for_roll(
                    issuer,
                    issue,
                    earlier_maturity,
                    settlement,
                    coupon_pct=coupon_pct,
                    face_amount=face_amount,
                    zero_coupon=zero_coupon,
                )
                rolled_yld = self._bond_yield_on_curve(
                    rolled_bond, issuer, curve_handle
                )
                rolls[f"roll_{label}_spotyield"] = spot_yld_pct - rolled_yld

            forward_settlement = _to_ql_date(
                tenor.add_to(_from_ql_date(settlement), issuer)
            )
            if forward_settlement >= maturity:
                rolls[f"roll_{label}_fwdyield"] = None
            else:
                forward_bond = self._build_bond_for_roll(
                    issuer,
                    issue,
                    maturity,
                    forward_settlement,
                    coupon_pct=coupon_pct,
                    face_amount=face_amount,
                    zero_coupon=zero_coupon,
                )
                forward_yld = self._bond_yield_on_curve(
                    forward_bond,
                    issuer,
                    curve_handle,
                    evaluation_date=forward_settlement,
                )
                rolls[f"roll_{label}_fwdyield"] = spot_yld_pct - forward_yld
        return rolls

    def _bond_metrics_from_priced_bond(
        self,
        bond: ql.Bond,
        issuer: IssuerProfile,
        settlement: ql.Date,
        *,
        curve_handle: ql.YieldTermStructureHandle | None = None,
        include_accrued: bool = True,
        issue_date: ql.Date | None = None,
        maturity_date: ql.Date | None = None,
        coupon_pct: float | None = None,
        face_amount: float = 100.0,
        zero_coupon: bool = False,
        repo_term_structure: NumericTermStructure | None = None,
    ) -> FixedIncomeAnalyticsOutput:
        bond_settlement = _bond_settlement(issuer, settlement)
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

        carry_1m = None
        carry_3m = None
        carry_6m = None
        carry_1y = None
        if repo_term_structure is not None:
            repo_term_structure = repo_term_structure.filter({'1m', '3m', '6m', '1y'})
            if len(repo_term_structure) > 0:
                carry_1m = repo_term_structure.rates.get('1m', None)
                if carry_1m is not None:
                    carry_1m = yld - carry_1m 
                carry_3m = repo_term_structure.rates.get('3m', None)
                if carry_3m is not None:
                    carry_3m = yld - carry_3m 
                carry_6m = repo_term_structure.rates.get('6m', None)
                if carry_6m is not None:
                    carry_6m = yld - carry_6m 
                carry_1y = repo_term_structure.rates.get('1y', None)
                if carry_1y is not None:
                    carry_1y = yld - carry_1y 
            pass

        z_spread_bps = None
        par_yield = None
        zero_rate = None
        roll_1m_spotyield = None
        roll_3m_spotyield = None
        roll_6m_spotyield = None
        roll_1y_spotyield = None
        roll_1m_fwdyield = None
        roll_3m_fwdyield = None
        roll_6m_fwdyield = None
        roll_1y_fwdyield = None
        if curve_handle is not None:
            curve = curve_handle.currentLink()
            z_spread_bps = (
                ql.BondFunctions.zSpread(
                    bond,
                    ql.BondPrice(clean, ql.BondPrice.Clean),
                    curve,
                    ql.Compounded,
                    issuer.frequency,
                )
                * 10_000.0
            )
            par_yield = (
                ql.BondFunctions.atmRate(
                    bond,
                    curve,
                    bond_settlement,
                    ql.BondPrice(100.0, ql.BondPrice.Clean),
                )
                * 100.0
            )
            zero_rate = (
                curve_handle.zeroRate(
                    bond.maturityDate(),
                    issuer.day_count,
                    ql.Compounded,
                    issuer.frequency,
                ).rate()
                * 100.0
            )
            maturity = maturity_date or bond.maturityDate()
            if issue_date is not None and maturity > bond_settlement:
                roll_values = self._compute_yield_rolls(
                    yld * 100.0,
                    issuer,
                    bond_settlement,
                    issue_date,
                    maturity,
                    curve_handle,
                    coupon_pct=coupon_pct,
                    face_amount=face_amount,
                    zero_coupon=zero_coupon,
                )
                roll_1m_spotyield = roll_values["roll_1m_spotyield"]
                roll_3m_spotyield = roll_values["roll_3m_spotyield"]
                roll_6m_spotyield = roll_values["roll_6m_spotyield"]
                roll_1y_spotyield = roll_values["roll_1y_spotyield"]
                roll_1m_fwdyield = roll_values["roll_1m_fwdyield"]
                roll_3m_fwdyield = roll_values["roll_3m_fwdyield"]
                roll_6m_fwdyield = roll_values["roll_6m_fwdyield"]
                roll_1y_fwdyield = roll_values["roll_1y_fwdyield"]
        
        carry_roll_1m_spotyield = carry_1m - roll_1m_spotyield if carry_1m is not None and roll_1m_spotyield is not None else None
        carry_roll_3m_spotyield = carry_3m - roll_3m_spotyield if carry_3m is not None and roll_3m_spotyield is not None else None
        carry_roll_6m_spotyield = carry_6m - roll_6m_spotyield if carry_6m is not None and roll_6m_spotyield is not None else None
        carry_roll_1y_spotyield = carry_1y - roll_1y_spotyield if carry_1y is not None and roll_1y_spotyield is not None else None
        carry_roll_1m_fwdyield = carry_1m - roll_1m_fwdyield if carry_1m is not None and roll_1m_fwdyield is not None else None
        carry_roll_3m_fwdyield = carry_3m - roll_3m_fwdyield if carry_3m is not None and roll_3m_fwdyield is not None else None
        carry_roll_6m_fwdyield = carry_6m - roll_6m_fwdyield if carry_6m is not None and roll_6m_fwdyield is not None else None
        carry_roll_1y_fwdyield = carry_1y - roll_1y_fwdyield if carry_1y is not None and roll_1y_fwdyield is not None else None

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
            par_yield=par_yield,
            zero_rate=zero_rate,
            roll_1m_spotyield=roll_1m_spotyield,
            roll_3m_spotyield=roll_3m_spotyield,
            roll_6m_spotyield=roll_6m_spotyield,
            roll_1y_spotyield=roll_1y_spotyield,
            roll_1m_fwdyield=roll_1m_fwdyield,
            roll_3m_fwdyield=roll_3m_fwdyield,
            roll_6m_fwdyield=roll_6m_fwdyield,
            roll_1y_fwdyield=roll_1y_fwdyield,
            carry_1m=carry_1m,
            carry_3m=carry_3m,
            carry_6m=carry_6m,
            carry_1y=carry_1y,
            carry_roll_1m_spotyield=carry_roll_1m_spotyield,
            carry_roll_3m_spotyield=carry_roll_3m_spotyield,
            carry_roll_6m_spotyield=carry_roll_6m_spotyield,
            carry_roll_1y_spotyield=carry_roll_1y_spotyield,
            carry_roll_1m_fwdyield=carry_roll_1m_fwdyield,
            carry_roll_3m_fwdyield=carry_roll_3m_fwdyield,
            carry_roll_6m_fwdyield=carry_roll_6m_fwdyield,
            carry_roll_1y_fwdyield=carry_roll_1y_fwdyield,
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
