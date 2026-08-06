"""Bond future contract conventions and dated contracts.

:data:`BOND_FUTURE_CONVENTIONS` is a static registry of exchange-traded
government bond future templates, keyed by the canonical exchange code.  A
:class:`BondFuture` pairs one of those templates with a delivery month and
year, e.g. ``BondFuture.parse("IKU9")`` for the September 2029 Long-Term
Euro-BTP contract.

Repo conventions are *not* stored here — they are resolved from the underlying
:class:`~cqfi.issuers.IssuerProfile` via :class:`RepoMarket`, so a
day count has exactly one home.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import QuantLib as ql
from decorules import HasRulesActions, raise_if_false_on_instance

from cqfi.date_utils import add_months, whole_months_between
from cqfi.day_of_month import DayOfMonthSpec
from cqfi.instruments import Bond
from cqfi.issuers import ISSUERS, IssuerProfile, RepoMarket, resolve_issuer

MONTHS_PER_YEAR = 12

# Futures delivery month codes (CME/Eurex/ICE/OSE share this convention).
MONTH_CODES: dict[int, str] = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}
MONTH_CODE_TO_MONTH: dict[str, int] = {v: k for k, v in MONTH_CODES.items()}

# Government bond futures trade on the quarterly cycle everywhere.
QUARTERLY_MONTHS: tuple[int, ...] = (3, 6, 9, 12)

_MONTH_LETTERS = "".join(MONTH_CODES.values())
_CONTRACT_RE = re.compile(
    rf"^(?P<code>[A-Z0-9_]+?)(?P<month>[{_MONTH_LETTERS}])(?P<year>\d{{1,4}})$"
)
_MONTH_YEAR_RE = re.compile(rf"^(?P<month>[{_MONTH_LETTERS}])(?P<year>\d{{1,4}})?$")
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")


class ConversionFactorMethod(str, Enum):
    """Which exchange's published conversion-factor formula applies."""

    CME = "CME"
    EUREX = "EUREX"
    ICE = "ICE"
    JGB = "JGB"


class BondFutureError(ValueError):
    """Raised when a bond future code or delivery month cannot be resolved."""


class AmbiguousBondFutureError(BondFutureError):
    """Raised when a synonym maps to more than one contract.

    Attributes:
        candidates: Canonical names the synonym could refer to.
    """

    def __init__(self, code: str, candidates: tuple[str, ...]) -> None:
        self.candidates = candidates
        super().__init__(
            f"Bond future code {code!r} is ambiguous; it matches "
            f"{', '.join(candidates)}. Qualify it as e.g. "
            f"'{candidates[0].split('_')[0]}' or "
            f"'<EXCHANGE>:{code.upper()}'."
        )


def months(years: int, extra_months: int = 0) -> int:
    """Return a term in whole months, e.g. ``months(1, 9)`` for 1y9m."""
    return years * MONTHS_PER_YEAR + extra_months


@dataclass(frozen=True)
class MaturityRange:
    """An inclusive term window measured in whole months.

    ``None`` on either bound means unrestricted in that direction.
    """

    min_months: int | None = None
    max_months: int | None = None

    def contains(self, term_months: int) -> bool:
        """Whether *term_months* whole months falls inside this window."""
        if self.min_months is not None and term_months < self.min_months:
            return False
        if self.max_months is not None and term_months > self.max_months:
            return False
        return True

    def bounds(self, anchor: date) -> tuple[date | None, date | None]:
        """Return the absolute maturity window this term implies from *anchor*."""
        return (
            None if self.min_months is None else add_months(anchor, self.min_months),
            None if self.max_months is None else add_months(anchor, self.max_months),
        )

    def contains_maturity(self, anchor: date, maturity: date) -> bool:
        """Whether a bond maturing on *maturity* has an admissible term.

        Compares absolute dates rather than whole months so that a bond
        maturing a day beyond the cap is correctly excluded.
        """
        earliest, latest = self.bounds(anchor)
        if earliest is not None and maturity < earliest:
            return False
        return latest is None or maturity <= latest

    @property
    def is_unrestricted(self) -> bool:
        """Whether this window admits every term."""
        return self.min_months is None and self.max_months is None

    def as_dict(self) -> dict[str, int | None]:
        return {"min_months": self.min_months, "max_months": self.max_months}

    def __str__(self) -> str:
        def _fmt(value: int | None) -> str:
            if value is None:
                return "*"
            return f"{value // MONTHS_PER_YEAR}y{value % MONTHS_PER_YEAR}m"

        return f"{_fmt(self.min_months)}..{_fmt(self.max_months)}"


@dataclass(frozen=True)
class BasketRestrictions:
    """Eligibility rules a bond must satisfy to be deliverable.

    Restrictions are declarative named fields rather than callables so that a
    convention stays hashable and JSON-serialisable.
    """

    remaining_maturity: MaturityRange = MaturityRange()
    original_term: MaturityRange | None = None
    min_issue_amount: float | None = None
    exclude_green: bool = False

    def admits(self, bond: Bond, delivery_date: date) -> tuple[bool, str | None]:
        """Check *bond* against every restriction as of *delivery_date*.

        Args:
            bond: Candidate deliverable bond.
            delivery_date: Date remaining maturity is measured from.

        Returns:
            ``(True, None)`` when admissible, else ``(False, reason)``.
        """
        if not self.remaining_maturity.contains_maturity(delivery_date, bond.maturity):
            remaining = whole_months_between(delivery_date, bond.maturity)
            return False, (
                f"remaining term {remaining}m outside {self.remaining_maturity}"
            )

        if self.original_term is not None:
            if bond.issue_date is None:
                return False, "original term restricted but issue_date is missing"
            if not self.original_term.contains_maturity(bond.issue_date, bond.maturity):
                original = whole_months_between(bond.issue_date, bond.maturity)
                return False, f"original term {original}m outside {self.original_term}"

        if self.min_issue_amount is not None and bond.issue_amount is not None:
            if bond.issue_amount < self.min_issue_amount:
                return False, (
                    f"issue amount {bond.issue_amount:,.0f} below "
                    f"minimum {self.min_issue_amount:,.0f}"
                )

        if self.exclude_green and bond.is_green:
            return False, "green bonds are not deliverable"

        return True, None

    def as_dict(self) -> dict[str, object]:
        return {
            "remaining_maturity": self.remaining_maturity.as_dict(),
            "original_term": (
                None if self.original_term is None else self.original_term.as_dict()
            ),
            "min_issue_amount": self.min_issue_amount,
            "exclude_green": self.exclude_green,
        }


@dataclass(frozen=True)
@raise_if_false_on_instance(
    lambda inst: inst.issuer_code in ISSUERS,
    ValueError,
    "issuer_code must be a key of ISSUERS",
)
@raise_if_false_on_instance(
    lambda inst: inst.contract_size > 0,
    ValueError,
    "contract_size must be positive",
)
@raise_if_false_on_instance(
    lambda inst: 0 < inst.notional_coupon < 100,
    ValueError,
    "notional_coupon must be a percentage in (0, 100)",
)
@raise_if_false_on_instance(
    lambda inst: inst.cf_decimals >= 0,
    ValueError,
    "cf_decimals must be non-negative",
)
class BondFutureConvention(metaclass=HasRulesActions):
    """Static contract terms for one exchange-traded bond future.

    Attributes:
        name: Canonical uppercase exchange code; the registry key.
        exchange: Listing venue, one of ``CME``, ``EUREX``, ``OSE``, ``ICE``.
        issuer_code: Underlying sovereign issuer; a key of ``ISSUERS``.
        notional_maturity_years: Maturity of the notional bond, in years.
        notional_coupon: Notional coupon in percent, e.g. ``6.0``.
        contract_size: Nominal per contract in the issuer's currency.
        reference_day: Day the conversion factor is calculated to.
        delivery_start: First day of the delivery period.
        delivery_end: Last day of the delivery period.
        conversion_factor_method: Which exchange formula computes the factor.
        repo_market: Which of the issuer's repo conventions to finance at.
        cf_decimals: Decimals the exchange publishes conversion factors to.
        bloomberg_root: Bloomberg ticker root, when it differs from ``name``.
        synonyms: Other codes that resolve to this contract.
        restrictions: Delivery basket eligibility rules.
    """

    name: str
    exchange: str
    issuer_code: str
    notional_maturity_years: float
    notional_coupon: float
    contract_size: float
    reference_day: DayOfMonthSpec
    delivery_start: DayOfMonthSpec
    delivery_end: DayOfMonthSpec
    conversion_factor_method: ConversionFactorMethod
    repo_market: RepoMarket = RepoMarket.INTERNATIONAL
    cf_decimals: int = 4
    bloomberg_root: str | None = None
    synonyms: tuple[str, ...] = ()
    restrictions: BasketRestrictions = field(default_factory=BasketRestrictions)

    def issuer(self) -> IssuerProfile:
        """Return the underlying issuer's profile."""
        return ISSUERS[self.issuer_code]

    def calendar(self) -> ql.Calendar:
        """Return the issuer's holiday calendar."""
        return self.issuer().calendar()

    def currency(self) -> str:
        """Return the contract currency."""
        return self.issuer().currency

    def repo_day_count(self) -> ql.DayCounter:
        """Return the day count financing accrues on, from the issuer."""
        return self.issuer().repo_conventions(self.repo_market)[0]

    def repo_settlement_days(self) -> int:
        """Return the repo settlement lag, from the issuer."""
        return self.issuer().repo_conventions(self.repo_market)[1]

    def reference_date(self, year: int, month: int) -> date:
        """Return the conversion-factor reference date for a delivery month."""
        return self.reference_day.resolve(year, month, self.calendar())

    def delivery_start_date(self, year: int, month: int) -> date:
        """Return the first delivery day for a delivery month."""
        return self.delivery_start.resolve(year, month, self.calendar())

    def delivery_end_date(self, year: int, month: int) -> date:
        """Return the last delivery day for a delivery month."""
        return self.delivery_end.resolve(year, month, self.calendar())

    def display_root(self) -> str:
        """Return the ticker root used when rendering a dated contract.

        Prefers the Bloomberg root, but falls back to the canonical exchange
        code when that root is shared with another contract — so that
        ``BondFuture.parse(str(future))`` always round-trips.
        """
        if self.bloomberg_root is None:
            return self.name
        if _resolves_uniquely_to(self.bloomberg_root, self.name):
            return self.bloomberg_root
        return self.name

    def all_codes(self) -> tuple[str, ...]:
        """Return every code that may refer to this contract."""
        codes = [self.name, *self.synonyms]
        if self.bloomberg_root is not None:
            codes.append(self.bloomberg_root)
        return tuple(dict.fromkeys(codes))

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of the contract terms."""
        return {
            "name": self.name,
            "exchange": self.exchange,
            "bloomberg_root": self.bloomberg_root,
            "synonyms": list(self.synonyms),
            "issuer": self.issuer_code,
            "currency": self.currency(),
            "notional_maturity_years": self.notional_maturity_years,
            "notional_coupon": self.notional_coupon,
            "contract_size": self.contract_size,
            "reference_day": str(self.reference_day),
            "delivery_start": str(self.delivery_start),
            "delivery_end": str(self.delivery_end),
            "conversion_factor_method": self.conversion_factor_method.value,
            "cf_decimals": self.cf_decimals,
            "repo_market": self.repo_market.value,
            "repo_day_count": self.repo_day_count().name(),
            "repo_settlement_days": self.repo_settlement_days(),
            "restrictions": self.restrictions.as_dict(),
        }

    def as_json(self, **kwargs) -> str:
        """Return the contract terms as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)

    def __str__(self) -> str:
        """Return the canonical uppercase exchange code."""
        return self.name.upper()

    def __repr__(self) -> str:
        """Return a detailed JSON representation."""
        return self.as_json(indent=2)


# --------------------------------------------------------------------------- #
# Registry construction helpers
# --------------------------------------------------------------------------- #
# CME and ICE compute conversion factors to the *unadjusted* first calendar day
# of the delivery month; shifting it would change the whole-quarter rounding.
_FIRST_UNADJUSTED = DayOfMonthSpec.from_string("C1 0BD")
_FIRST_BUSINESS = DayOfMonthSpec.from_string("C1 1BD")
_LAST_BUSINESS = DayOfMonthSpec.from_string("L0 -1BD")
# Eurex delivers on the 10th calendar day, rolled forward when not a business day.
_EUREX_DELIVERY = DayOfMonthSpec.from_string("C10 1BD")
# Osaka delivers on the 20th calendar day, rolled forward likewise.
_OSE_DELIVERY = DayOfMonthSpec.from_string("C20 1BD")


def _cme(
    name: str,
    bloomberg: str,
    notional_years: float,
    remaining: MaturityRange,
    *,
    contract_size: float = 100_000.0,
    original_term: MaturityRange | None = None,
    synonyms: tuple[str, ...] = (),
) -> BondFutureConvention:
    """Build a CME US Treasury future convention (6% notional, 4 dp)."""
    return BondFutureConvention(
        name=name,
        exchange="CME",
        issuer_code="USA",
        notional_maturity_years=notional_years,
        notional_coupon=6.0,
        contract_size=contract_size,
        reference_day=_FIRST_UNADJUSTED,
        delivery_start=_FIRST_BUSINESS,
        delivery_end=_LAST_BUSINESS,
        conversion_factor_method=ConversionFactorMethod.CME,
        cf_decimals=4,
        bloomberg_root=bloomberg,
        synonyms=synonyms,
        restrictions=BasketRestrictions(
            remaining_maturity=remaining, original_term=original_term
        ),
    )


def _eurex(
    name: str,
    bloomberg: str,
    issuer_code: str,
    notional_years: float,
    remaining: MaturityRange,
    *,
    notional_coupon: float = 6.0,
    contract_size: float = 100_000.0,
) -> BondFutureConvention:
    """Build a Eurex government bond future convention (6 dp, 10th delivery)."""
    return BondFutureConvention(
        name=name,
        exchange="EUREX",
        issuer_code=issuer_code,
        notional_maturity_years=notional_years,
        notional_coupon=notional_coupon,
        contract_size=contract_size,
        reference_day=_EUREX_DELIVERY,
        delivery_start=_EUREX_DELIVERY,
        delivery_end=_EUREX_DELIVERY,
        conversion_factor_method=ConversionFactorMethod.EUREX,
        cf_decimals=6,
        bloomberg_root=bloomberg,
        restrictions=BasketRestrictions(remaining_maturity=remaining),
    )


def _ose(
    name: str,
    bloomberg: str,
    notional_years: float,
    notional_coupon: float,
    remaining: MaturityRange,
    *,
    contract_size: float = 100_000_000.0,
    synonyms: tuple[str, ...] = (),
    repo_market: RepoMarket = RepoMarket.INTERNATIONAL,
) -> BondFutureConvention:
    """Build an Osaka Exchange JGB future convention (6 dp, 20th delivery)."""
    return BondFutureConvention(
        name=name,
        exchange="OSE",
        issuer_code="JPN",
        notional_maturity_years=notional_years,
        notional_coupon=notional_coupon,
        contract_size=contract_size,
        reference_day=_OSE_DELIVERY,
        delivery_start=_OSE_DELIVERY,
        delivery_end=_OSE_DELIVERY,
        conversion_factor_method=ConversionFactorMethod.JGB,
        repo_market=repo_market,
        cf_decimals=6,
        bloomberg_root=bloomberg,
        synonyms=synonyms,
        restrictions=BasketRestrictions(remaining_maturity=remaining),
    )


def _ice(
    name: str,
    bloomberg: str,
    notional_years: float,
    notional_coupon: float,
    remaining: MaturityRange,
    *,
    contract_size: float = 100_000.0,
) -> BondFutureConvention:
    """Build an ICE Futures Europe gilt future convention (7 dp)."""
    return BondFutureConvention(
        name=name,
        exchange="ICE",
        issuer_code="GBR",
        notional_maturity_years=notional_years,
        notional_coupon=notional_coupon,
        contract_size=contract_size,
        reference_day=_FIRST_UNADJUSTED,
        delivery_start=_FIRST_BUSINESS,
        delivery_end=_LAST_BUSINESS,
        conversion_factor_method=ConversionFactorMethod.ICE,
        cf_decimals=7,
        bloomberg_root=bloomberg,
        restrictions=BasketRestrictions(remaining_maturity=remaining),
    )


# Original-term cap shared by the CME 2-, 3- and 5-year note contracts.
_CME_SHORT_ORIGINAL_TERM = MaturityRange(max_months=months(5, 3))

_CONVENTIONS: tuple[BondFutureConvention, ...] = (
    # ------------------------------------------------------------------ #
    # CME — US Treasury futures
    # ------------------------------------------------------------------ #
    _cme(
        "ZT",
        "TU",
        2,
        MaturityRange(months(1, 9), months(2)),
        contract_size=200_000.0,
        original_term=_CME_SHORT_ORIGINAL_TERM,
    ),
    _cme(
        "Z3N",
        "3Y",
        3,
        MaturityRange(months(2, 9), months(3)),
        contract_size=200_000.0,
        original_term=_CME_SHORT_ORIGINAL_TERM,
        synonyms=("3YR",),
    ),
    _cme(
        "ZF",
        "FV",
        5,
        MaturityRange(months(4, 2), months(5, 3)),
        original_term=_CME_SHORT_ORIGINAL_TERM,
    ),
    _cme("ZN", "TY", 10, MaturityRange(months(6, 6), months(10))),
    _cme("TN", "UXY", 10, MaturityRange(months(9, 5), months(10))),
    _cme("TWE", "TWE", 20, MaturityRange(months(19, 2), months(19, 11))),
    _cme("ZB", "US", 30, MaturityRange(months(15), months(25))),
    _cme("UB", "WN", 30, MaturityRange(months(25), months(30))),
    # ------------------------------------------------------------------ #
    # Eurex — German, Italian, French, Spanish, Swiss and EU bond futures
    # ------------------------------------------------------------------ #
    _eurex("FGBS", "DU", "DEU", 2, MaturityRange(months(1, 9), months(2, 3))),
    _eurex("FGBM", "OE", "DEU", 5, MaturityRange(months(4, 6), months(5, 6))),
    _eurex("FGBL", "RX", "DEU", 10, MaturityRange(months(8, 6), months(10, 6))),
    # The Buxl contract is the Eurex outlier: a 4% notional coupon.
    _eurex(
        "FGBX",
        "UB",
        "DEU",
        30,
        MaturityRange(months(24), months(35)),
        notional_coupon=4.0,
    ),
    _eurex("FBTS", "BTS", "ITA", 3, MaturityRange(months(2), months(3, 3))),
    _eurex("FBTM", "FBM", "ITA", 5, MaturityRange(months(4, 6), months(6))),
    _eurex("FBTP", "IK", "ITA", 10, MaturityRange(months(8, 6), months(11))),
    _eurex("FOAM", "FOA", "FRA", 5, MaturityRange(months(4, 6), months(5, 6))),
    _eurex("FOAT", "FM", "FRA", 10, MaturityRange(months(8, 6), months(10, 6))),
    _eurex("FBON", "EON", "ESP", 10, MaturityRange(months(8, 6), months(10, 6))),
    _eurex("CONF", "SW", "CHE", 10, MaturityRange(months(8), months(13))),
    _eurex("FBEU", "EUB", "EU", 10, MaturityRange(months(8), months(12))),
    # ------------------------------------------------------------------ #
    # Osaka Exchange — JGB futures (international and domestic repo legs)
    # ------------------------------------------------------------------ #
    _ose(
        "FJG5",
        "JB5",
        5,
        3.0,
        MaturityRange(months(4), months(5, 3)),
        synonyms=("5JGB",),
    ),
    _ose(
        "FJGB", "JB", 10, 6.0, MaturityRange(months(7), months(11)), synonyms=("10JGB",)
    ),
    _ose(
        "FJG2",
        "JL",
        20,
        6.0,
        MaturityRange(months(19, 3), months(21)),
        synonyms=("20JGB",),
    ),
    _ose(
        "FJG5_DOM",
        "JB5_DOM",
        5,
        3.0,
        MaturityRange(months(4), months(5, 3)),
        synonyms=("5JGB_DOM",),
        repo_market=RepoMarket.DOMESTIC,
    ),
    _ose(
        "FJGB_DOM",
        "JB_DOM",
        10,
        6.0,
        MaturityRange(months(7), months(11)),
        synonyms=("10JGB_DOM",),
        repo_market=RepoMarket.DOMESTIC,
    ),
    _ose(
        "FJG2_DOM",
        "JL_DOM",
        20,
        6.0,
        MaturityRange(months(19, 3), months(21)),
        synonyms=("20JGB_DOM",),
        repo_market=RepoMarket.DOMESTIC,
    ),
    # ------------------------------------------------------------------ #
    # ICE Futures Europe — UK gilt futures
    # ------------------------------------------------------------------ #
    _ice("G", "G", 2, 3.0, MaturityRange(months(1, 6), months(3, 3))),
    _ice("H", "WX", 5, 4.0, MaturityRange(months(4), months(6, 3))),
    _ice("R", "G", 10, 4.0, MaturityRange(months(8, 9), months(13))),
    _ice("U", "UB", 30, 4.0, MaturityRange(months(28), months(37))),
)

BOND_FUTURE_CONVENTIONS: dict[str, BondFutureConvention] = {
    convention.name: convention for convention in _CONVENTIONS
}

if len(BOND_FUTURE_CONVENTIONS) != len(_CONVENTIONS):  # pragma: no cover
    raise RuntimeError("duplicate canonical bond future codes in the registry")


def _build_synonym_index() -> dict[str, tuple[str, ...]]:
    """Map every non-canonical code to the contracts that claim it."""
    index: dict[str, list[str]] = {}
    for convention in _CONVENTIONS:
        for code in convention.all_codes():
            if code == convention.name:
                continue
            index.setdefault(code, []).append(convention.name)
    return {code: tuple(names) for code, names in index.items()}


_SYNONYM_INDEX: dict[str, tuple[str, ...]] = _build_synonym_index()


def _resolves_uniquely_to(code: str, name: str) -> bool:
    """Whether *code* refers to *name* and nothing else."""
    if code in BOND_FUTURE_CONVENTIONS:
        return code == name
    return _SYNONYM_INDEX.get(code, ()) == (name,)


def resolve_bond_future_convention(
    code: str,
    *,
    exchange: str | None = None,
    issuer: str | None = None,
) -> BondFutureConvention:
    """Look up a contract template by canonical code, synonym or Bloomberg root.

    A canonical exchange code always wins outright, so ``"UB"`` is the CME
    Ultra Bond even though Eurex Buxl and the ICE Ultra Gilt share that
    Bloomberg root.  Reach the others by canonical code (``"FGBX"``) or by
    qualifying the synonym (``"EUREX:UB"`` or ``exchange="EUREX"``).

    Args:
        code: Contract code, optionally prefixed ``EXCHANGE:``.
        exchange: Restrict candidates to this listing venue.
        issuer: Restrict candidates to this issuer code.

    Returns:
        The matching contract template.

    Raises:
        AmbiguousBondFutureError: If several contracts remain after filtering.
        BondFutureError: If no contract matches.
    """
    key = code.strip().upper()
    if not key:
        raise BondFutureError("bond future code must not be empty")

    if ":" in key:
        prefix, _, key = key.partition(":")
        exchange = exchange or prefix
        key = key.strip()

    exchange = exchange.strip().upper() if exchange else None
    issuer_code = resolve_issuer(issuer).source_code if issuer else None

    def _matches(convention: BondFutureConvention) -> bool:
        if exchange is not None and convention.exchange != exchange:
            return False
        return issuer_code is None or convention.issuer_code == issuer_code

    exact = BOND_FUTURE_CONVENTIONS.get(key)
    if exact is not None and _matches(exact):
        return exact

    candidates = tuple(
        name
        for name in _SYNONYM_INDEX.get(key, ())
        if _matches(BOND_FUTURE_CONVENTIONS[name])
    )
    if len(candidates) == 1:
        return BOND_FUTURE_CONVENTIONS[candidates[0]]
    if len(candidates) > 1:
        raise AmbiguousBondFutureError(key, candidates)

    raise BondFutureError(
        f"Unknown bond future code {code!r}. Supported codes: "
        f"{', '.join(sorted(BOND_FUTURE_CONVENTIONS))}"
    )


# --------------------------------------------------------------------------- #
# Delivery month resolution
# --------------------------------------------------------------------------- #
def _earliest_delivery(today: date) -> tuple[int, int]:
    """Return the month after *today*'s, the earliest deliverable month.

    Contracts never default to the current month, so the search always starts
    from the 1st of the next month.
    """
    return (
        (today.year + 1, 1)
        if today.month == MONTHS_PER_YEAR
        else (
            today.year,
            today.month + 1,
        )
    )


def _first_month_at_or_after(
    earliest: tuple[int, int],
    *,
    allowed_months: tuple[int, ...],
    year_last_digit: int | None = None,
) -> tuple[int, int]:
    """Return the first allowed ``(year, month)`` at or after *earliest*."""
    year, month = earliest
    # Twenty years is ample: the tightest filter is one month in a decade.
    for offset in range(20 * MONTHS_PER_YEAR):
        index = (year * MONTHS_PER_YEAR + month - 1) + offset
        candidate_year, candidate_month = (
            index // MONTHS_PER_YEAR,
            index % MONTHS_PER_YEAR + 1,
        )
        if candidate_month not in allowed_months:
            continue
        if year_last_digit is not None and candidate_year % 10 != year_last_digit:
            continue
        return candidate_year, candidate_month
    raise BondFutureError(  # pragma: no cover - unreachable with the filters above
        "no delivery month satisfies the requested constraints"
    )


def default_delivery_month(today: date | None = None) -> tuple[int, int]:
    """Return the first quarterly delivery month after the current month."""
    return _first_month_at_or_after(
        _earliest_delivery(today or date.today()), allowed_months=QUARTERLY_MONTHS
    )


def resolve_delivery_month(
    token: str | None, *, today: date | None = None
) -> tuple[int, int]:
    """Resolve a delivery-month token to ``(year, month)``.

    Accepted forms, all relative to the month after *today*:

    ==============  ===============================================
    ``None`` / ``""``  Next quarterly month
    ``"U"``            Next September
    ``"M8"``           June of the next year ending in 8
    ``"6"``            Next quarterly month in a year ending in 6
    ``"U2020"``        September 2020
    ``"2020-09"``      September 2020
    ==============  ===============================================

    Args:
        token: The delivery specifier, or ``None`` for the default.
        today: Reference date; defaults to the current date.

    Returns:
        The resolved ``(year, month)`` pair.

    Raises:
        BondFutureError: If *token* is not one of the accepted forms.
    """
    today = today or date.today()
    earliest = _earliest_delivery(today)

    text = (token or "").strip().upper()
    if not text:
        return _first_month_at_or_after(earliest, allowed_months=QUARTERLY_MONTHS)

    if (iso := _ISO_MONTH_RE.match(text)) is not None:
        year, month = int(iso.group("year")), int(iso.group("month"))
        if not 1 <= month <= MONTHS_PER_YEAR:
            raise BondFutureError(f"invalid delivery month {token!r}")
        return year, month

    if text.isdigit() and len(text) == 1:
        return _first_month_at_or_after(
            earliest, allowed_months=QUARTERLY_MONTHS, year_last_digit=int(text)
        )

    if (match := _MONTH_YEAR_RE.match(text)) is not None:
        month = MONTH_CODE_TO_MONTH[match.group("month")]
        year_text = match.group("year")
        if year_text is None:
            return _first_month_at_or_after(earliest, allowed_months=(month,))
        if len(year_text) == 4:
            return int(year_text), month
        if len(year_text) == 1:
            return _first_month_at_or_after(
                earliest, allowed_months=(month,), year_last_digit=int(year_text)
            )

    raise BondFutureError(
        f"invalid delivery specifier {token!r}; expected e.g. "
        "'M8', 'U', '6', 'U2020' or '2020-09'"
    )


@dataclass(frozen=True)
class BondFuture:
    """A dated bond future contract: a convention plus a delivery month."""

    convention: BondFutureConvention
    delivery_month: int
    delivery_year: int

    def __post_init__(self) -> None:
        if not 1 <= self.delivery_month <= MONTHS_PER_YEAR:
            raise BondFutureError(
                f"delivery_month must be 1..12, got {self.delivery_month}"
            )

    @classmethod
    def default(
        cls, convention: BondFutureConvention, *, today: date | None = None
    ) -> BondFuture:
        """Build the front quarterly contract, never the current month."""
        year, month = default_delivery_month(today)
        return cls(convention=convention, delivery_month=month, delivery_year=year)

    @classmethod
    def parse(cls, text: str, *, today: date | None = None) -> BondFuture:
        """Parse ``"IKU9"``, ``"FBTP"`` or ``"FGBS U2020"`` into a contract.

        Args:
            text: A contract code, optionally with a delivery specifier
                appended directly (``"IKU9"``) or separated by whitespace.
            today: Reference date used to resolve relative specifiers.

        Returns:
            The dated contract.

        Raises:
            BondFutureError: If the code or delivery specifier is invalid.
        """
        parts = text.strip().upper().split()
        if not parts:
            raise BondFutureError("bond future code must not be empty")
        if len(parts) > 2:
            raise BondFutureError(f"invalid bond future specifier {text!r}")

        code, token = parts[0], parts[1] if len(parts) == 2 else None
        if token is None:
            code, token = _split_contract_code(code)

        convention = resolve_bond_future_convention(code)
        year, month = resolve_delivery_month(token, today=today)
        return cls(convention=convention, delivery_month=month, delivery_year=year)

    def month_code(self) -> str:
        """Return the single-letter delivery month code."""
        return MONTH_CODES[self.delivery_month]

    def reference_date(self) -> date:
        """Return the conversion-factor reference date."""
        return self.convention.reference_date(self.delivery_year, self.delivery_month)

    def delivery_start_date(self) -> date:
        """Return the first delivery day."""
        return self.convention.delivery_start_date(
            self.delivery_year, self.delivery_month
        )

    def delivery_end_date(self) -> date:
        """Return the last delivery day."""
        return self.convention.delivery_end_date(
            self.delivery_year, self.delivery_month
        )

    def notional_bond_maturity(self) -> date:
        """Return the maturity of the contract's notional bond.

        Measured as the end of the delivery period plus the convention's
        notional maturity in years.
        """
        return add_months(
            self.delivery_end_date(),
            round(self.convention.notional_maturity_years * MONTHS_PER_YEAR),
        )

    def as_dict(self) -> dict[str, object]:
        """Return the full contract structure, ready for JSON."""
        return {
            "contract": str(self),
            "delivery_month": self.delivery_month,
            "delivery_year": self.delivery_year,
            "reference_date": self.reference_date().isoformat(),
            "delivery_start_date": self.delivery_start_date().isoformat(),
            "delivery_end_date": self.delivery_end_date().isoformat(),
            "notional_bond_maturity": self.notional_bond_maturity().isoformat(),
            "convention": self.convention.as_dict(),
        }

    def as_json(self, **kwargs) -> str:
        """Return the full contract structure as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)

    def __str__(self) -> str:
        """Return the uppercase contract code with delivery month and year.

        Format: ``<CODE><MONTH><YEAR_DIGIT>``, e.g., ``IKU9`` for Sept 2029 Euro-BTP.
        """
        root = self.convention.display_root().upper()
        return f"{root}{self.month_code()}{self.delivery_year % 10}"

    def __repr__(self) -> str:
        """Return a detailed JSON representation of the contract."""
        return self.as_json(indent=2)


def _split_contract_code(text: str) -> tuple[str, str | None]:
    """Split ``"IKU9"`` into ``("IK", "U9")``, leaving bare codes untouched."""
    match = _CONTRACT_RE.match(text)
    if match is None:
        return text, None
    code = match.group("code")
    try:
        resolve_bond_future_convention(code)
    except BondFutureError:
        return text, None
    return code, f"{match.group('month')}{match.group('year')}"
