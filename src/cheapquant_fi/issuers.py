"""Issuer metadata for government bond curve conventions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable

import QuantLib as ql

from decorules import (
    HasRulesActions,
    raise_if_false_on_instance,
)

class RateType(str, Enum):
    ZERO = "zero"
    PAR = "par"


@dataclass(frozen=True)
class ExDividendConvention:
    """Generalised ex-dividend (ex-coupon) window for coupon-bearing bonds.

    Wraps the four trailing ``exCoupon*`` parameters that QuantLib's
    ``FixedRateBond`` and ``FixedRateBondHelper`` accept.  Create one instance
    per issuer that has an ex-dividend period and attach it to the corresponding
    ``IssuerProfile.ex_dividend`` field.

    UK gilts: 7 business days before each coupon payment date (DMO definition).
    During this window QuantLib makes accrued interest negative, correctly
    reflecting that the coupon rebate flows from buyer back to seller.
    """

    period: ql.Period
    calendar_factory: Callable[[], ql.Calendar]
    convention: int = ql.Unadjusted
    end_of_month: bool = False

    def calendar(self) -> ql.Calendar:
        return self.calendar_factory()

    def ex_coupon_args(self) -> tuple:
        """Return the four trailing QuantLib ex-coupon constructor args.

        Unpack with ``*issuer.ex_dividend.ex_coupon_args()`` when calling
        ``ql.FixedRateBond`` or ``ql.FixedRateBondHelper``.
        """
        return (self.period, self.calendar(), self.convention, self.end_of_month)


_NO_EX_COUPON: tuple = (ql.Period(), ql.NullCalendar(), ql.Unadjusted, False)


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


@dataclass(frozen=True)
@raise_if_false_on_instance(lambda inst: inst.settlement_days >= 0,
                              ValueError, "settlement_days must be non-negative")
class IssuerProfile(metaclass=HasRulesActions):
    """QuantLib calendar and market conventions for a sovereign issuer."""

    source_code: str
    name: str
    currency: str
    calendar_factory: Callable[[], ql.Calendar]
    day_count: ql.DayCounter
    settlement_days: int = 1
    frequency: int = ql.Semiannual
    default_rate_type: RateType = RateType.ZERO
    ex_dividend: ExDividendConvention | None = None
    repo_day_count: ql.DayCounter | None = None
    repo_settlement_days: int | None = None

    def calendar(self) -> ql.Calendar:
        return self.calendar_factory()

    def settlement_date(self, trade_date: date) -> date:
        """Return settlement date as trade date plus ``settlement_days`` business days."""
        ql_settlement = _to_ql_date(trade_date)
        calendar = self.calendar()
        for _ in range(self.settlement_days):
            ql_settlement = calendar.advance(
                ql_settlement, 1, ql.Days, ql.Following
            )
        return _from_ql_date(ql_settlement)

    def make_QL_fixed_rate_bond(
        self,
        schedule: ql.Schedule,
        coupon_rates: list[float],
        redemption: float = 100.0,
        issue_date: ql.Date = ql.Date(),
    ) -> ql.FixedRateBond:
        """Construct a ``FixedRateBond`` with ex-dividend applied when configured.

        This is the single call site for coupon bond construction; any pricing
        code should use this factory rather than calling ``ql.FixedRateBond``
        directly, so ex-dividend behaviour is picked up automatically for
        issuers that define it (e.g. GBR).
        """
        ex_args = (
            self.ex_dividend.ex_coupon_args()
            if self.ex_dividend is not None
            else _NO_EX_COUPON
        )
        return ql.FixedRateBond(
            self.settlement_days,
            100.0,
            schedule,
            coupon_rates,
            self.day_count,
            ql.ModifiedFollowing,
            redemption,
            issue_date,
            ql.NullCalendar(),
            *ex_args,
        )


ISSUERS: dict[str, IssuerProfile] = {
    # ------------------------------------------------------------------ #
    # Americas
    # ------------------------------------------------------------------ #
    "USA": IssuerProfile(
        source_code="USA",
        name="United States",
        currency="USD",
        # US Treasury market calendar (Columbus Day / Veterans Day removed)
        calendar_factory=lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
        day_count=ql.ActualActual(ql.ActualActual.Bond),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
        # Repo (Treasury GC / SOFR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "BRA": IssuerProfile(
        source_code="BRA",
        name="Brazil",
        currency="BRL",
        # NTN-F (fixed-rate federal bonds): Brazilian settlement calendar, T+1
        calendar_factory=lambda: ql.Brazil(ql.Brazil.Settlement),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "CAN": IssuerProfile(
        source_code="CAN",
        name="Canada",
        currency="CAD",
        # Government of Canada bonds: semi-annual Act/Act, T+2
        calendar_factory=lambda: ql.Canada(ql.Canada.Settlement),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    # ------------------------------------------------------------------ #
    # Europe — EUR sovereign (TARGET calendar, Act/Act ISDA, T+2, Annual)
    # ------------------------------------------------------------------ #
    "AUT": IssuerProfile(
        source_code="AUT",
        name="Austria",
        currency="EUR",
        # Austrian Federal Government bonds (OEBs): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "BEL": IssuerProfile(
        source_code="BEL",
        name="Belgium",
        currency="EUR",
        # OLOs (Obligations Linéaires): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "DEU": IssuerProfile(
        source_code="DEU",
        name="Germany",
        currency="EUR",
        # Bunds/Bobls/Schätze: annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "ESP": IssuerProfile(
        source_code="ESP",
        name="Spain",
        currency="EUR",
        # Bonos del Estado / Obligaciones: annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "FRA": IssuerProfile(
        source_code="FRA",
        name="France",
        currency="EUR",
        # OATs (Obligations Assimilables du Trésor): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "GBR": IssuerProfile(
        source_code="GBR",
        name="United Kingdom",
        currency="GBP",
        # Gilts: semi-annual Act/Act ISDA, T+1, 7-business-day ex-dividend
        calendar_factory=lambda: ql.UnitedKingdom(ql.UnitedKingdom.Settlement),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
        ex_dividend=ExDividendConvention(
            period=ql.Period(7, ql.Days),
            calendar_factory=lambda: ql.UnitedKingdom(ql.UnitedKingdom.Settlement),
            convention=ql.Unadjusted,
            end_of_month=False,
        ),
        # Repo (Gilt GC / SONIA-referenced): Act/365 Fixed, same-day (T+0) settlement
        repo_day_count=ql.Actual365Fixed(),
        repo_settlement_days=0,
    ),
    "GRC": IssuerProfile(
        source_code="GRC",
        name="Greece",
        currency="EUR",
        # Greek government bonds (GGBs): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "IRL": IssuerProfile(
        source_code="IRL",
        name="Ireland",
        currency="EUR",
        # Irish Government bonds: annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "ITA": IssuerProfile(
        source_code="ITA",
        name="Italy",
        currency="EUR",
        # BTPs (Buoni del Tesoro Poliennali): SEMI-annual coupon — the main
        # exception among Euro sovereigns; TARGET calendar, T+2
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "NLD": IssuerProfile(
        source_code="NLD",
        name="Netherlands",
        currency="EUR",
        # DSLs (Dutch State Loans): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "PRT": IssuerProfile(
        source_code="PRT",
        name="Portugal",
        currency="EUR",
        # OTs (Obrigações do Tesouro): annual coupon, TARGET
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
        # Repo (Eurozone GC / €STR-referenced): Act/360, same-day (T+0) settlement
        repo_day_count=ql.Actual360(),
        repo_settlement_days=0,
    ),
    "RUS": IssuerProfile(
        source_code="RUS",
        name="Russia",
        currency="RUB",
        # OFZ (федеральный займ): semi-annual coupon, Act/Act ISDA, T+1
        calendar_factory=lambda: ql.Russia(ql.Russia.Settlement),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "CHE": IssuerProfile(
        source_code="CHE",
        name="Switzerland",
        currency="CHF",
        # Swiss Confederation bonds: annual coupon, Act/Act ISDA, T+2
        calendar_factory=lambda: ql.Switzerland(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
    ),
    # ------------------------------------------------------------------ #
    # Asia-Pacific
    # ------------------------------------------------------------------ #
    "AUS": IssuerProfile(
        source_code="AUS",
        name="Australia",
        currency="AUD",
        # ACGBs (Australian Commonwealth Government Bonds): semi-annual,
        # Act/Act ISDA, T+2
        calendar_factory=lambda: ql.Australia(ql.Australia.Settlement),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "CHN": IssuerProfile(
        source_code="CHN",
        name="China",
        currency="CNY",
        # CGBs (Chinese Government Bonds): annual coupon, Act/Act ISDA,
        # T+1 on the interbank bond market (IB)
        calendar_factory=lambda: ql.China(ql.China.IB),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
    ),
    "IND": IssuerProfile(
        source_code="IND",
        name="India",
        currency="INR",
        # G-Secs (Government Securities): semi-annual, Act/Act ISDA, T+1
        calendar_factory=lambda: ql.India(ql.India.NSE),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "JPN": IssuerProfile(
        source_code="JPN",
        name="Japan",
        currency="JPY",
        # JGBs (Japanese Government Bonds): semi-annual, Act/Act ISDA, T+2
        calendar_factory=lambda: ql.Japan(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
        # Repo (JGB GC / TONAR-referenced): Act/365, same-day (T+0) settlement
        repo_day_count=ql.Actual365Fixed(),
        repo_settlement_days=0,
    ),
    "KOR": IssuerProfile(
        source_code="KOR",
        name="Korea",
        currency="KRW",
        # KTBs (Korea Treasury Bonds): semi-annual, Act/Act ISDA, T+1
        calendar_factory=lambda: ql.SouthKorea(ql.SouthKorea.KRX),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
}

# Aliases for natural-language / country names from ycs_data.yaml vocabulary.
SOURCE_ALIASES: dict[str, str] = {
    # Americas
    "US": "USA",
    "UST": "USA",
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "TREASURIES": "USA",
    "BRAZIL": "BRA",
    "CANADA": "CAN",
    # Europe — EUR sovereigns
    "AUSTRIA": "AUT",
    "BELGIUM": "BEL",
    "GERMANY": "DEU",
    "DE": "DEU",
    "BUND": "DEU",
    "BUNDS": "DEU",
    "BOBL": "DEU",
    "SCHATZ": "DEU",
    "SPAIN": "ESP",
    "BONO": "ESP",
    "BONOS DEL ESTADO": "ESP",
    "OBLIGACIONES": "ESP",
    "FRANCE": "FRA",
    "OAT": "FRA",
    "OATS": "FRA",
    "OBLIGATIONS ASSIMILABLES DU TRESOR": "FRA",
    "FRTF": "FRA",
    "UK": "GBR",
    "UNITED KINGDOM": "GBR",
    "GILTS": "GBR",
    "GILT": "GBR",
    "GREECE": "GRC",
    "IRELAND": "IRL",
    "ITALY": "ITA",
    "BTP": "ITA",
    "BTPS": "ITA",
    "BUONI DEL TESORO POLIENNALI": "ITA",
    "BUONI": "ITA",
    "NETHERLANDS": "NLD",
    "PORTUGAL": "PRT",
    "RUSSIA": "RUS",
    "OFZ": "RUS",
    "SWITZERLAND": "CHE",
    "SWISS": "CHE",    
    # Asia-Pacific
    "AUSTRALIA": "AUS",
    "ACGB": "AUS",
    "ACGBS": "AUS",
    "CHINA": "CHN",
    "CGB": "CHN",
    "CGBS": "CHN",
    "INDIA": "IND",
    "JAPAN": "JPN",
    "JGB": "JPN",
    "JGBS": "JPN",
    "KOREA": "KOR",
    "SOUTH KOREA": "KOR",
    "KTB": "KOR",
    "KTBS": "KOR",
}

def resolve_issuer(source_or_name: str) -> IssuerProfile:
    key = source_or_name.strip().upper()
    code = SOURCE_ALIASES.get(key, key)
    if code not in ISSUERS:
        supported = ", ".join(sorted(ISSUERS))
        raise ValueError(
            f"Unknown issuer {source_or_name!r}. Supported: {supported}"
        )
    return ISSUERS[code]
