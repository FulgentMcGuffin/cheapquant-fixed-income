"""Human-readable tenor strings for calendar time periods."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import QuantLib as ql

from cheapquant_fi.issuers import ISSUERS, IssuerProfile

_TENOR_TOKEN = re.compile(r"(\d+)(``|`|[^0-9\s])")

_UNIT_FIELDS: dict[str, str] = {
    "y": "years",
    "m": "months",
    "w": "weeks",
    "d": "days",
    "h": "hours",
    "`": "minutes",
    "``": "seconds",
}


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


@dataclass(frozen=True)
class Tenor:
    """A calendar time period built from additive year/month/week/day/hour/minute/second parts.

    Strings combine unsigned integers with unit suffixes (order-independent):

    - ``Y``/``y`` — years
    - ``M``/``m`` — months
    - ``W``/``w`` — weeks
    - ``D``/``d`` — days
    - ``H``/``h`` — hours
    - ````` — minutes
    - `````` — seconds

    Example: ``12y4M3w12d97h1`15``` → 12 years + 4 months + 3 weeks + 12 days
    + 97 hours + 1 minute + 15 seconds.
    """

    years: int = 0
    months: int = 0
    weeks: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0

    def __post_init__(self) -> None:
        for name in _UNIT_FIELDS.values():
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}")

    @classmethod
    def parse(cls, text: str) -> Tenor:
        """Parse a tenor string into component parts."""
        stripped = text.strip()
        if not stripped:
            raise ValueError("Tenor string must not be empty")

        totals = {field: 0 for field in _UNIT_FIELDS.values()}
        pos = 0
        for match in _TENOR_TOKEN.finditer(stripped):
            if match.start() != pos:
                raise ValueError(
                    f"Invalid tenor string {text!r} near {stripped[pos:]!r}"
                )
            pos = match.end()

            amount = int(match.group(1))
            unit = match.group(2)
            if unit not in ("`", "``"):
                unit = unit.lower()
            field = _UNIT_FIELDS.get(unit)
            if field is None:
                raise ValueError(f"Unknown tenor unit {unit!r} in {text!r}")
            totals[field] += amount

        if pos != len(stripped):
            raise ValueError(f"Invalid tenor string {text!r} near {stripped[pos:]!r}")
        if not any(totals.values()):
            raise ValueError(f"Tenor string must contain at least one component: {text!r}")

        return cls(**totals)

    @classmethod
    def from_string(cls, text: str) -> Tenor:
        """Alias for :meth:`parse`."""
        return cls.parse(text)

    def simplify(self) -> Tenor:
        """Return a new tenor with carried units (60s→m, 60m→h, 24h→d, 7d→w, 52w→y, 12m→y)."""
        seconds = self.seconds
        minutes = self.minutes + seconds // 60
        seconds %= 60
        hours = self.hours + minutes // 60
        minutes %= 60
        days = self.days + hours // 24
        hours %= 24
        weeks = self.weeks + days // 7
        days %= 7
        years = self.years + weeks // 52
        weeks %= 52
        years += self.months // 12
        months = self.months % 12
        return Tenor(
            years=years,
            months=months,
            weeks=weeks,
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )

    def _advance_on_calendar(
        self,
        ql_date: ql.Date,
        issuer: IssuerProfile,
    ) -> ql.Date:
        """Advance *ql_date* by simplified year/month/week/day components."""
        simplified = self.simplify()
        calendar = issuer.calendar()
        convention = ql.ModifiedFollowing

        if simplified.years:
            ql_date = calendar.advance(
                ql_date,
                ql.Period(simplified.years, ql.Years),
                convention,
                True,
            )
        if simplified.months:
            ql_date = calendar.advance(
                ql_date,
                ql.Period(simplified.months, ql.Months),
                convention,
                True,
            )
        if simplified.weeks:
            ql_date = calendar.advance(
                ql_date,
                ql.Period(simplified.weeks, ql.Weeks),
                convention,
                True,
            )
        if simplified.days:
            ql_date = ql_date + simplified.days
        return ql_date

    def add_to(
        self,
        when: date | datetime,
        issuer: IssuerProfile | None = None,
    ) -> date | datetime:
        """Return *when* advanced forward by this tenor.

        Year/month/week/day components use the issuer calendar with
        ``ModifiedFollowing`` and end-of-month handling.  Sub-day units are
        applied only when *when* is a :class:`~datetime.datetime`.
        """
        issuer = issuer or ISSUERS["DEU"]
        base_date = when.date() if isinstance(when, datetime) else when
        ql_end = self._advance_on_calendar(_to_ql_date(base_date), issuer)

        if isinstance(when, datetime):
            simplified = self.simplify()
            result = datetime.combine(
                _from_ql_date(ql_end),
                when.time(),
                tzinfo=when.tzinfo,
            )
            return result + timedelta(
                hours=simplified.hours,
                minutes=simplified.minutes,
                seconds=simplified.seconds,
            )

        return _from_ql_date(ql_end)

    def compare_to(
        self,
        other: Tenor,
        when: date | datetime,
        issuer: IssuerProfile | None = None,
    ) -> int:
        """Compare two tenors by their :meth:`add_to` results from *when*.

        Returns a negative value if ``self`` matures before *other*, zero if
        equal, and a positive value if ``self`` matures after *other*.  Use with
        :func:`functools.cmp_to_key` or sort by ``lambda t: t.add_to(when, issuer)``.
        """
        self_end = self.add_to(when, issuer)
        other_end = other.add_to(when, issuer)
        return (self_end > other_end) - (self_end < other_end)

    @classmethod
    def sort_key(
        cls,
        when: date | datetime,
        issuer: IssuerProfile | None = None,
    ) -> Callable[[Tenor], date | datetime]:
        """Return a key function for ordering tenors in a collection from *when*."""
        return lambda tenor: tenor.add_to(when, issuer)

    def days_tenor(
        self,
        start_date: date,
        issuer: IssuerProfile | None = None,
    ) -> Tenor:
        """Convert to a day-only tenor using the issuer calendar from *start_date*.

        The period is :meth:`simplified` first; sub-day units are ignored.  Years,
        months, and weeks are applied on the issuer calendar with
        ``ModifiedFollowing`` and end-of-month handling; plain days are added as
        calendar days.
        """
        issuer = issuer or ISSUERS["DEU"]
        ql_start = _to_ql_date(start_date)
        ql_end = self._advance_on_calendar(ql_start, issuer)
        return Tenor(days=int(ql_end - ql_start))

    def __str__(self) -> str:
        parts: list[str] = []
        if self.years:
            parts.append(f"{self.years}y")
        if self.months:
            parts.append(f"{self.months}m")
        if self.weeks:
            parts.append(f"{self.weeks}w")
        if self.days:
            parts.append(f"{self.days}d")
        if self.hours:
            parts.append(f"{self.hours}h")
        if self.minutes:
            parts.append(f"{self.minutes}`")
        if self.seconds:
            parts.append(f"{self.seconds}``")
        return "".join(parts)
