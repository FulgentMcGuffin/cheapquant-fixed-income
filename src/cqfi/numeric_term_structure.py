"""Numeric term structures keyed by :class:`~cqfi.tenor.Tenor`."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date

import QuantLib as ql

from cqfi.date_utils import from_ql_date, to_ql_date
from cqfi.issuers import ISSUERS, IssuerProfile
from cqfi.tenor import Tenor


class NumericTermStructure:
    """Mapping of repo tenors to rates, ordered by maturity from ``as_of``."""

    def __init__(
        self,
        pairs: Iterable[tuple[str, float]] | Mapping[str, float],
        as_of: date,
        to_decimal: bool = True,
    ) -> None:
        self.as_of = as_of
        if isinstance(pairs, Mapping):
            pairs = pairs.items()
        seen: dict[Tenor, str] = {}
        parsed: list[tuple[Tenor, float]] = []

        for label, rate in pairs:
            tenor = Tenor.parse(label).simplify()
            if tenor in seen:
                raise ValueError(
                    f"Duplicate tenor {tenor!s} "
                    f"(from {label!r} and {seen[tenor]!r})"
                )
            seen[tenor] = label
            parsed.append((tenor, rate))

        sort_key = Tenor.sort_key(as_of)
        self.rates: dict[Tenor, float] = {
            tenor: rate / 100.0 if to_decimal else rate
            for tenor, rate in sorted(parsed, key=lambda item: sort_key(item[0]))
        }

    def filter(
        self, acceptable_tenors: Iterable[str] = {"1m", "3m", "6m", "1y"}
    ) -> NumericTermStructure | None:
        """Return a new term structure with only the specified tenors."""
        if not acceptable_tenors:
            return self
        return NumericTermStructure(
            [
                (str(tenor), rate)
                for tenor, rate in self.rates.items()
                if str(tenor) in acceptable_tenors
            ],
            self.as_of,
            to_decimal=False,
        )

    def pillar_dates(self, issuer: IssuerProfile | None = None) -> list[date]:
        """Return the date each tenor matures on, ascending."""
        return [tenor.add_to(self.as_of, issuer) for tenor in self.rates]

    def rate_for(
        self,
        when: date,
        *,
        settlement_days: int = 0,
        issuer: IssuerProfile | None = None,
    ) -> float:
        """Return the rate for a term running from settlement to *when*.

        Interpolates linearly in time between the bracketing pillars and
        extrapolates flat beyond either end, which is the conventional
        treatment for a short repo curve with only a handful of points.

        Args:
            when: Maturity of the term being priced.
            settlement_days: Business days between ``as_of`` and the start of
                a quoted tenor, from the issuer's repo conventions.
            issuer: Calendar used to roll tenors; defaults to the one
                :meth:`Tenor.add_to` uses.

        Returns:
            The rate as a decimal, matching how rates are stored.

        Raises:
            ValueError: If the term structure is empty.
        """
        if not self.rates:
            raise ValueError("cannot interpolate an empty term structure")

        start = self._settlement_date(settlement_days, issuer)
        pillars = self.pillar_dates(issuer)
        rates = list(self.rates.values())

        target = (when - start).days
        offsets = [(pillar - start).days for pillar in pillars]

        if target <= offsets[0]:
            return rates[0]
        if target >= offsets[-1]:
            return rates[-1]

        for index in range(1, len(offsets)):
            if target <= offsets[index]:
                span = offsets[index] - offsets[index - 1]
                if span == 0:
                    return rates[index]
                weight = (target - offsets[index - 1]) / span
                return rates[index - 1] + weight * (rates[index] - rates[index - 1])
        return rates[-1]  # pragma: no cover - guarded by the bounds above

    def _settlement_date(
        self, settlement_days: int, issuer: IssuerProfile | None
    ) -> date:
        """Return ``as_of`` rolled forward by the repo settlement lag."""
        if settlement_days <= 0:
            return self.as_of
        profile = issuer if issuer is not None else ISSUERS["DEU"]
        calendar = profile.calendar()
        rolled = to_ql_date(self.as_of)
        for _ in range(settlement_days):
            rolled = calendar.advance(rolled, 1, ql.Days, ql.Following)
        return from_ql_date(rolled)

    def to_dict(self) -> dict[str, float]:
        """Return tenor labels and rates ordered by increasing maturity."""
        return {str(tenor): rate for tenor, rate in self.rates.items()}

    def to_json(self, **kwargs) -> str:
        """Return the term structure as a JSON object string."""
        return json.dumps(self.to_dict(), **kwargs)

    def __len__(self) -> int:
        return len(self.rates)
