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
    ) -> tuple[FixedIncomeAnalyticsOutput, FixedIncomeAnalyticsOutput | None, FixedIncomeAnalyticsOutput | None]:
        """Return bond analytics and optional maturity-matched CMT analytics and optional maturity-matched fixed-coupon CMT analytics from input.

        The CMT uses the bond's settlement and maturity, the bond's curve
        ``par_yield`` as coupon, and :meth:`IssuerProfile.as_unadjusted`
        conventions (no holiday calendar / no payment-date adjustment / no ex-dividend adjustment).
        
        The fixed-coupon CMT uses the bond's settlement and maturity, the input coupon as the bond's coupon, and :meth:`IssuerProfile.as_unadjusted`
        conventions (no holiday calendar / no payment-date adjustment / no ex-dividend adjustment).
        """
        issuer = resolve_issuer(request.issuer)
        settlement = _to_ql_date(request.settlement_date)
        ql.Settings.instance().evaluationDate = settlement

        qlbond = self._build_fixed_rate_bond(issuer, request)
        curve_handle = self._bond_curve(market, issuer.source_code, curve_label)
        use_curve = self._uses_curve(request)

        if use_curve:
            qlbond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))
            maturity = _to_ql_date(request.maturity_date)
            metrics = self._bond_metrics_from_priced_bond(
                qlbond,
                issuer,
                settlement,
                curve_handle=curve_handle,
                issue_date=_to_ql_date(request.issue_date or request.settlement_date),
                maturity_date=maturity,
                coupon_pct=request.coupon,
                face_amount=request.face_amount,
                repo_term_structure=request.repo_term_structure,
            )
            cmt_metrics = None
            if metrics.par_yield is not None:
                cmt_issuer = issuer.as_unadjusted()
                cmt_bond = self._build_maturity_matched_cmt(
                    cmt_issuer,
                    settlement,
                    maturity,
                    metrics.par_yield,
                    face_amount=request.face_amount,
                )
                cmt_bond.setPricingEngine(ql.DiscountingBondEngine(curve_handle))
                cmt_metrics = self._bond_metrics_from_priced_bond(
                    cmt_bond,
                    cmt_issuer,
                    settlement,
                    curve_handle=curve_handle,
                    issue_date=settlement,
                    maturity_date=maturity,
                    coupon_pct=metrics.par_yield,
                    face_amount=request.face_amount,
                    repo_term_structure=None,
                )
                fc_cmt_metrics = self._bond_metrics_from_input(
                    cmt_bond,
                    cmt_issuer,
                    settlement,
                    curve_handle=curve_handle,
                    issue_date=settlement,
                    maturity_date=maturity,
                    coupon_pct=request.coupon,
                    face_amount=request.face_amount,
                    repo_term_structure=None,
                )                
            return metrics, cmt_metrics, fc_cmt_metrics

        metrics = self._bond_metrics_from_input(
            qlbond, issuer, settlement, request.input_column, request.input_value
        )
        return metrics, None, None

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
        convention = issuer.payment_convention
        issue = _to_ql_date(request.issue_date or request.settlement_date)
        maturity = _to_ql_date(request.maturity_date)

        schedule = ql.Schedule(
            issue,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            convention,
            convention,
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
            return calendar.advance(settlement, days, ql.Days, issuer.payment_convention)
        return calendar.advance(
            settlement,
            ql.Period(int(round(tenor_years * 12)), ql.Months),
            issuer.payment_convention,
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
        convention = issuer.payment_convention
        maturity = self._cmt_maturity(issuer, settlement, tenor_years)

        if coupon is None:
            return ql.ZeroCouponBond(
                issuer.settlement_days,
                calendar,
                100.0,
                maturity,
                convention,
                100.0,
                settlement,
            )

        schedule = ql.Schedule(
            settlement,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            convention,
            convention,
            ql.DateGeneration.Backward,
            False,
        )
        return issuer.make_QL_fixed_rate_bond(
            schedule,
            [coupon / 100.0],
            issue_date=settlement,
        )

    def _build_maturity_matched_cmt(
        self,
        issuer: IssuerProfile,
        settlement: ql.Date,
        maturity: ql.Date,
        coupon_pct: float,
        *,
        face_amount: float = 100.0,
    ) -> ql.FixedRateBond:
        """Build a fixed-coupon CMT with the bond's maturity and settlement.

        *issuer* should normally be :meth:`IssuerProfile.as_unadjusted` so coupon
        dates are not business-day adjusted.
        """
        calendar = issuer.calendar()
        convention = issuer.payment_convention
        schedule = ql.Schedule(
            settlement,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            convention,
            convention,
            ql.DateGeneration.Backward,
            False,
        )
        return issuer.make_QL_fixed_rate_bond(
            schedule,
            [coupon_pct / 100.0],
            redemption=face_amount,
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
        convention = issuer.payment_convention
        if zero_coupon or coupon_pct is None:
            return ql.ZeroCouponBond(
                issuer.settlement_days,
                calendar,
                face_amount,
                maturity,
                convention,
                face_amount,
                settlement,
            )

        schedule = ql.Schedule(
            issue,
            maturity,
            ql.Period(issuer.frequency),
            calendar,
            convention,
            convention,
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

        _carry_labels = ("1m", "3m", "6m", "1y")
        _repo_rates: dict[str, float | None] = dict.fromkeys(_carry_labels)
        if repo_term_structure is not None:
            filtered = repo_term_structure.filter(set(_carry_labels))
            if filtered:
                repo_rates = filtered.to_dict()
                for label in _carry_labels:
                    _repo_rates[label] = repo_rates.get(label)

        def _carry_from_repo(rate: float | None) -> float | None:
            return (yld - rate) * 100.0 if rate is not None else None

        carry_1m, carry_3m, carry_6m, carry_1y = (
            _carry_from_repo(_repo_rates[label]) for label in _carry_labels
        )
        repo_rate_1m, repo_rate_3m, repo_rate_6m, repo_rate_1y = (
            _repo_rates[label] for label in _carry_labels
        )

        z_spread_bps = par_yield = zero_rate = None
        rolls = {
            f"roll_{label}_{kind}": None
            for label in _carry_labels
            for kind in ("spotyield", "fwdyield")
        }
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
            if not zero_coupon and coupon_pct not in (None, 0.0):
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
                rolls.update(
                    self._compute_yield_rolls(
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
                )

        _carries = dict(
            zip(_carry_labels, (carry_1m, carry_3m, carry_6m, carry_1y), strict=True)
        )
        carry_rolls = {
            f"carry_roll_{label}_{kind}": (
                _carries[label] - rolls[f"roll_{label}_{kind}"]
                if _carries[label] is not None
                and rolls[f"roll_{label}_{kind}"] is not None
                else None
            )
            for label in _carry_labels
            for kind in ("spotyield", "fwdyield")
        }

        return FixedIncomeAnalyticsOutput(
            yield_to_maturity=yld * 100.0,
            clean_price=clean,
            dirty_price=dirty,
            accrued_interest=accrued if include_accrued else None,
            duration=macaulay,
            convexity=convexity,
            dv01_sensitivity=bpv,
            gamma_sensitivity=(
                convexity * clean / 100.0 if convexity is not None else None
            ),
            z_spread=z_spread_bps,
            par_yield=par_yield,
            zero_rate=zero_rate,
            repo_rate_1m=repo_rate_1m,
            repo_rate_3m=repo_rate_3m,
            repo_rate_6m=repo_rate_6m,
            repo_rate_1y=repo_rate_1y,
            carry_1m=carry_1m,
            carry_3m=carry_3m,
            carry_6m=carry_6m,
            carry_1y=carry_1y,
            **rolls,
            **carry_rolls,
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
            clean = ql.BondFunctions.cleanPrice(bond, yld, dc, comp, freq, settlement)
        else:
            clean = input_value
            yld = ql.BondFunctions.bondYield(
                bond,
                ql.BondPrice(clean, ql.BondPrice.Clean),
                dc,
                comp,
                freq,
                settlement,
            )

        dirty = clean + ql.BondFunctions.accruedAmount(bond, settlement)
        accrued = dirty - clean if include_accrued else 0.0

        macaulay = ql.BondFunctions.duration(
            bond, yld, dc, comp, freq, ql.Duration.Macaulay, settlement
        )
        convexity = ql.BondFunctions.convexity(bond, yld, dc, comp, freq, settlement)
        bpv = ql.BondFunctions.basisPointValue(bond, yld, dc, comp, freq, settlement)

        return FixedIncomeAnalyticsOutput(
            yield_to_maturity=yld * 100.0,
            clean_price=clean,
            dirty_price=dirty,
            accrued_interest=accrued if include_accrued else None,
            duration=macaulay,
            convexity=convexity,
            dv01_sensitivity=bpv,
            gamma_sensitivity=(
                convexity * clean / 100.0 if convexity is not None else None
            ),
        )
