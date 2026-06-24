"""Issuer metadata for government bond curve conventions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

import QuantLib as ql


class RateType(str, Enum):
    ZERO = "zero"
    PAR = "par"


@dataclass(frozen=True)
class IssuerProfile:
    """QuantLib calendar and market conventions for a sovereign issuer."""

    source_code: str
    name: str
    currency: str
    calendar_factory: Callable[[], ql.Calendar]
    day_count: ql.DayCounter
    settlement_days: int = 1
    frequency: int = ql.Semiannual
    default_rate_type: RateType = RateType.ZERO

    def calendar(self) -> ql.Calendar:
        return self.calendar_factory()


ISSUERS: dict[str, IssuerProfile] = {
    "USA": IssuerProfile(
        source_code="USA",
        name="United States",
        currency="USD",
        calendar_factory=lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
        day_count=ql.ActualActual(ql.ActualActual.Bond),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "DEU": IssuerProfile(
        source_code="DEU",
        name="Germany",
        currency="EUR",
        calendar_factory=lambda: ql.TARGET(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Annual,
        default_rate_type=RateType.ZERO,
    ),
    "GBR": IssuerProfile(
        source_code="GBR",
        name="United Kingdom",
        currency="GBP",
        calendar_factory=lambda: ql.UnitedKingdom(ql.UnitedKingdom.GovernmentBond),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=1,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
    "JPN": IssuerProfile(
        source_code="JPN",
        name="Japan",
        currency="JPY",
        calendar_factory=lambda: ql.Japan(),
        day_count=ql.ActualActual(ql.ActualActual.ISDA),
        settlement_days=2,
        frequency=ql.Semiannual,
        default_rate_type=RateType.ZERO,
    ),
}

# Aliases for natural-language / country names from input_data vocabulary.
SOURCE_ALIASES: dict[str, str] = {
    "US": "USA",
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "GERMANY": "DEU",
    "DE": "DEU",
    "UK": "GBR",
    "UNITED KINGDOM": "GBR",
    "GILTS": "GBR",
    "JAPAN": "JPN",
    "JGB": "JPN",
    "JGBS": "JPN",
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
