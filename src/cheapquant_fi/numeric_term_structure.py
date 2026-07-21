"""Numeric term structures keyed by :class:`~cheapquant_fi.tenor.Tenor`."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date

from cheapquant_fi.tenor import Tenor


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

    def to_dict(self) -> dict[str, float]:
        """Return tenor labels and rates ordered by increasing maturity."""
        return {str(tenor): rate for tenor, rate in self.rates.items()}

    def to_json(self, **kwargs) -> str:
        """Return the term structure as a JSON object string."""
        return json.dumps(self.to_dict(), **kwargs)

    def __len__(self) -> int:
        return len(self.rates)
