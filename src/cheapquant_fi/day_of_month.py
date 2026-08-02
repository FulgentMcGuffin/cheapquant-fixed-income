"""String specifications for a day of the month, with holiday adjustment.

A specification has two independent parts:

* a **calendar-day rule** picking an unadjusted date within a month —
  ``"C1"`` (1st), ``"C20"`` (20th), ``"L0"`` (last day), ``"L9"`` (10th from
  last), ``"WED3"`` (3rd Wednesday), ``"FRI1"`` (1st Friday);
* a **holiday-adjustment rule** applied *only when* that date falls on a
  weekend or holiday — ``"0BD"`` (none), ``"1BD"`` (next business day),
  ``"-1BD"`` (previous business day), ``"1FRI"`` (next Friday, business day or
  not), ``"1FRIBD"`` (next Friday that is a business day).

Bond future contracts use these to express delivery and conversion-factor
reference dates, e.g. ``DayOfMonthSpec.from_string("C10 1BD")``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import QuantLib as ql

from cheapquant_fi.date_utils import days_in_month, from_ql_date, to_ql_date

# Weekday names as used in specification strings, mapped to date.weekday().
WEEKDAY_NAMES: dict[str, int] = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}
_WEEKDAY_BY_INDEX: dict[int, str] = {v: k for k, v in WEEKDAY_NAMES.items()}
_WEEKDAY_ALTERNATIVES = "|".join(WEEKDAY_NAMES)

# Highest ordinal accepted for "n-th weekday of the month".  A 5th occurrence
# is not guaranteed to exist in every month, so it is rejected outright.
MAX_WEEKDAY_ORDINAL = 4

# Guards a weekday search that a pathological calendar would never terminate.
_MAX_WEEKLY_STEPS = 8

_CAL_DAY_RE = re.compile(
    r"^(?:C(?P<forward>\d{1,2})"
    r"|L(?P<backward>\d{1,2})"
    rf"|(?P<weekday>{_WEEKDAY_ALTERNATIVES})(?P<nth>\d))$"
)

_ADJ_RE = re.compile(
    r"^(?P<sign>[+-])?(?P<n>\d{1,2})"
    rf"(?P<weekday>{_WEEKDAY_ALTERNATIVES})?(?P<bd>BD)?$"
)


class DayOfMonthSpecError(ValueError):
    """Raised when a day-of-month specification is malformed or unresolvable."""


@dataclass(frozen=True)
class CalendarDayRule:
    """An unadjusted day within a month.

    Attributes:
        kind: ``"forward"`` counts calendar days from the 1st (1-based),
            ``"backward"`` counts from the last day (0-based), ``"weekday"``
            picks the n-th occurrence of a weekday.
        n: The ordinal, interpreted according to *kind*.
        weekday: Target ``date.weekday()`` value; set only for ``"weekday"``.
    """

    kind: Literal["forward", "backward", "weekday"]
    n: int
    weekday: int | None = None

    def __post_init__(self) -> None:
        if self.kind == "forward" and self.n < 1:
            raise DayOfMonthSpecError(
                f"calendar day must be 1 or greater, got {self.n}"
            )
        if self.kind == "backward" and self.n < 0:
            raise DayOfMonthSpecError(
                f"days from month end must be 0 or greater, got {self.n}"
            )
        if self.kind == "weekday":
            if self.weekday is None:
                raise DayOfMonthSpecError("weekday rule requires a weekday")
            if not 1 <= self.n <= MAX_WEEKDAY_ORDINAL:
                raise DayOfMonthSpecError(
                    f"weekday ordinal must be 1..{MAX_WEEKDAY_ORDINAL}, got {self.n}"
                )

    @classmethod
    def parse(cls, text: str) -> CalendarDayRule:
        """Parse ``"C20"``, ``"L0"`` or ``"WED3"`` into a rule."""
        match = _CAL_DAY_RE.match(text.strip().upper())
        if match is None:
            raise DayOfMonthSpecError(
                f"invalid calendar day rule {text!r}; expected e.g. "
                "'C1', 'C20', 'L0', 'L9', 'WED3'"
            )
        if (forward := match.group("forward")) is not None:
            return cls(kind="forward", n=int(forward))
        if (backward := match.group("backward")) is not None:
            return cls(kind="backward", n=int(backward))
        return cls(
            kind="weekday",
            n=int(match.group("nth")),
            weekday=WEEKDAY_NAMES[match.group("weekday")],
        )

    def day_in_month(self, year: int, month: int) -> date:
        """Return the unadjusted date this rule selects in *month* of *year*."""
        length = days_in_month(year, month)
        if self.kind == "forward":
            day = self.n
        elif self.kind == "backward":
            day = length - self.n
        else:
            first_weekday = date(year, month, 1).weekday()
            offset = (self.weekday - first_weekday) % 7
            day = 1 + offset + 7 * (self.n - 1)

        if not 1 <= day <= length:
            raise DayOfMonthSpecError(
                f"{self} is out of range for {year:04d}-{month:02d} " f"({length} days)"
            )
        return date(year, month, day)

    def __str__(self) -> str:
        if self.kind == "forward":
            return f"C{self.n}"
        if self.kind == "backward":
            return f"L{self.n}"
        return f"{_WEEKDAY_BY_INDEX[self.weekday]}{self.n}"


@dataclass(frozen=True)
class HolidayAdjustmentRule:
    """How to move a date that falls on a weekend or holiday.

    Attributes:
        count: Signed number of steps; ``0`` means no adjustment at all.
        weekday: When set, step to occurrences of this weekday rather than to
            business days.
        business_day: Whether the result must be a business day.  Always true
            for pure ``nBD`` rules; for weekday rules it is the ``BD`` suffix.
    """

    count: int = 0
    weekday: int | None = None
    business_day: bool = True

    def __post_init__(self) -> None:
        if self.count == 0 and self.weekday is not None:
            raise DayOfMonthSpecError("a weekday adjustment needs a non-zero count")

    @property
    def is_identity(self) -> bool:
        """Whether this rule leaves every date unchanged."""
        return self.count == 0

    @classmethod
    def parse(cls, text: str) -> HolidayAdjustmentRule:
        """Parse ``"0BD"``, ``"2BD"``, ``"-1BD"``, ``"1FRI"`` or ``"-1WEDBD"``."""
        match = _ADJ_RE.match(text.strip().upper())
        if match is None:
            raise DayOfMonthSpecError(
                f"invalid holiday adjustment {text!r}; expected e.g. "
                "'0BD', '1BD', '-1BD', '2BD', '1FRI', '1FRIBD', '-1WEDBD'"
            )

        weekday_name = match.group("weekday")
        has_bd = match.group("bd") is not None
        if weekday_name is None and not has_bd:
            raise DayOfMonthSpecError(
                f"invalid holiday adjustment {text!r}; a weekday or 'BD' is required"
            )

        magnitude = int(match.group("n"))
        count = -magnitude if match.group("sign") == "-" else magnitude
        if count == 0:
            return cls()
        return cls(
            count=count,
            weekday=None if weekday_name is None else WEEKDAY_NAMES[weekday_name],
            business_day=has_bd if weekday_name is not None else True,
        )

    def adjust(self, when: date, calendar: ql.Calendar) -> date:
        """Apply this rule to *when* on *calendar*."""
        if self.is_identity:
            return when
        step = 1 if self.count > 0 else -1
        if self.weekday is None:
            return from_ql_date(
                calendar.advance(to_ql_date(when), self.count, ql.Days, ql.Following)
            )

        result = when
        for _ in range(abs(self.count)):
            result = _next_weekday(result, self.weekday, step)
        if not self.business_day:
            return result
        return _step_weeks_to_business_day(result, step, calendar)

    def __str__(self) -> str:
        if self.is_identity:
            return "0BD"
        if self.weekday is None:
            return f"{self.count}BD"
        suffix = "BD" if self.business_day else ""
        return f"{self.count}{_WEEKDAY_BY_INDEX[self.weekday]}{suffix}"


def _next_weekday(when: date, weekday: int, step: int) -> date:
    """Return the next *weekday* strictly after (or before) *when*."""
    result = when + timedelta(days=step)
    while result.weekday() != weekday:
        result += timedelta(days=step)
    return result


def _step_weeks_to_business_day(when: date, step: int, calendar: ql.Calendar) -> date:
    """Move *when* in whole weeks until it lands on a business day."""
    result = when
    for _ in range(_MAX_WEEKLY_STEPS):
        if calendar.isBusinessDay(to_ql_date(result)):
            return result
        result += timedelta(days=7 * step)
    raise DayOfMonthSpecError(
        f"no business day found within {_MAX_WEEKLY_STEPS} weeks of {when.isoformat()}"
    )


@dataclass(frozen=True)
class DayOfMonthSpec:
    """A calendar-day rule plus the adjustment applied on non-business days.

    The adjustment fires *only* when the unadjusted date is a weekend or
    holiday; a date that is already a business day is returned untouched.
    """

    calendar_rule: CalendarDayRule
    adjustment: HolidayAdjustmentRule = HolidayAdjustmentRule()

    @classmethod
    def parse(cls, day: str, adjustment: str = "0BD") -> DayOfMonthSpec:
        """Build a spec from its two component strings."""
        return cls(
            calendar_rule=CalendarDayRule.parse(day),
            adjustment=HolidayAdjustmentRule.parse(adjustment),
        )

    @classmethod
    def from_string(cls, text: str) -> DayOfMonthSpec:
        """Build a spec from a single ``"C10 1BD"``-style string."""
        parts = text.strip().split()
        if not 1 <= len(parts) <= 2:
            raise DayOfMonthSpecError(
                f"invalid day-of-month spec {text!r}; expected e.g. 'C10 1BD'"
            )
        return cls.parse(parts[0], parts[1] if len(parts) == 2 else "0BD")

    def resolve(self, year: int, month: int, calendar: ql.Calendar) -> date:
        """Return the adjusted date for *month* of *year* on *calendar*.

        The result may fall outside the requested month — ``"L0 1BD"`` on a
        month whose last day is a Sunday rolls into the following month.  This
        is intentional: the spec describes a date, not a month.
        """
        base = self.calendar_rule.day_in_month(year, month)
        if calendar.isBusinessDay(to_ql_date(base)):
            return base
        return self.adjustment.adjust(base, calendar)

    def resolve_for(self, when: date, calendar: ql.Calendar) -> date:
        """Resolve within the month containing *when*."""
        return self.resolve(when.year, when.month, calendar)

    def as_dict(self) -> dict[str, str]:
        """Return the two component strings."""
        return {
            "calendar_rule": str(self.calendar_rule),
            "adjustment": str(self.adjustment),
        }

    def __str__(self) -> str:
        return f"{self.calendar_rule} {self.adjustment}"
