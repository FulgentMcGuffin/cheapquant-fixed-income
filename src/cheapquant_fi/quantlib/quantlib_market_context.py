"""Market data containers for curves and FX used in QuantLib analytics.

Example
-------
Build a bond curve handle and register it on a :class:`MarketContext`::

    from datetime import date

    import polars as pl

    from cheapquant_fi.issuers import resolve_issuer
    from cheapquant_fi.quantlib.quantlib_curve import build_zero_curve
    from cheapquant_fi.quantlib.quantlib_market_context import CurveCollection, FXC, MarketContext

    as_of = date(2020, 1, 2)
    issuer = resolve_issuer("USA")

    rates_df = pl.DataFrame(
        {
            "tenor_column": ["Y001p0", "Y005p0", "Y010p0"],
            "tenor_label": ["1Y", "5Y", "10Y"],
            "tenor_years": [1.0, 5.0, 10.0],
            "rate_pct": [1.5, 2.0, 2.5],
        }
    )
    curve_handle, _ = build_zero_curve(issuer, as_of, rates_df)

    curves = CurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", curve_handle)

    fx = FXC(as_of=as_of)
    fx.set_rate("AUD", "USD", 1.45)

    ctx = MarketContext()
    ctx.add_curve_collection(curves)
    ctx.add_fxc(fx)

    # curve_handle is a ql.YieldTermStructureHandle; retrieve via issuer code:
    usa_curve = ctx.curve_collection().bond_curve("USA")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import QuantLib as ql


def _normalize_ccy(code: str) -> str:
    return code.strip().upper()


def _normalize_issuer(code: str) -> str:
    return code.strip().upper()


@dataclass
class FXC:
    """Cross table of FX rates as of a spot or forward date.

    :meth:`rate` takes two currency codes and returns how many units of the
    *first* currency are required for one unit of the *second* — e.g.
    ``rate("AUD", "USD")`` is the number of AUD needed to buy 1 USD, and
    ``rate("USD", "AUD")`` returns the reciprocal when only the AUD/USD quote
    has been stored.
    """

    as_of: date | datetime
    _rates: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)

    def set_rate(self, ccy_for: str, ccy_per: str, value: float) -> None:
        """Store *value* units of *ccy_for* per 1 unit of *ccy_per*."""
        base = _normalize_ccy(ccy_for)
        quote = _normalize_ccy(ccy_per)
        if base == quote:
            if value != 1.0:
                raise ValueError("Identity FX rate must be 1.0")
            return
        self._rates[(base, quote)] = float(value)

    def rate(self, ccy_for: str, ccy_per: str) -> float:
        """Return units of *ccy_for* required for 1 unit of *ccy_per*."""
        base = _normalize_ccy(ccy_for)
        quote = _normalize_ccy(ccy_per)
        if base == quote:
            return 1.0

        direct = self._rates.get((base, quote))
        if direct is not None:
            return direct

        inverse = self._rates.get((quote, base))
        if inverse is not None:
            if inverse == 0.0:
                raise ValueError(f"Cannot invert zero FX rate for {quote}/{base}")
            return 1.0 / inverse

        raise KeyError(f"No FX rate for {base}/{quote} as of {self.as_of}")

    def known_pairs(self) -> list[tuple[str, str]]:
        """Return stored (ccy_for, ccy_per) pairs (not reciprocals)."""
        return list(self._rates.keys())


@dataclass(frozen=True)
class SwapCurveKey:
    """Identifier for a swap curve (not yet implemented).

    Swap curves will be keyed by currency, fixed-coupon frequency, and the
    floating index name (e.g. SOFR, ESTR).
    """

    currency: str
    frequency: str
    index: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _normalize_ccy(self.currency))
        object.__setattr__(self, "frequency", self.frequency.strip().upper())
        object.__setattr__(self, "index", self.index.strip().upper())


@dataclass
class CurveCollection:
    """Yield curves as of a single valuation date or datetime.

    Bond curves are indexed by sovereign issuer code (``USA``, ``DEU``, …) as
    built by :func:`cheapquant_fi.quantlib.quantlib_curve.build_zero_curve`.  Swap curves
    will be indexed by :class:`SwapCurveKey` once implemented.
    """

    as_of: date | datetime
    _bond_curves: dict[str, ql.YieldTermStructureHandle] = field(
        default_factory=dict, repr=False
    )
    _swap_curves: dict[SwapCurveKey, ql.YieldTermStructureHandle] = field(
        default_factory=dict, repr=False
    )

    def set_bond_curve(
        self,
        issuer: str,
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> None:
        """Register a government bond curve for *issuer*."""
        code = _normalize_issuer(issuer)
        if isinstance(curve, ql.YieldTermStructureHandle):
            self._bond_curves[code] = curve
        else:
            self._bond_curves[code] = ql.YieldTermStructureHandle(curve)

    def bond_curve(self, issuer: str) -> ql.YieldTermStructureHandle:
        """Return the bond curve handle for *issuer*."""
        code = _normalize_issuer(issuer)
        try:
            return self._bond_curves[code]
        except KeyError as exc:
            raise KeyError(
                f"No bond curve for issuer {code!r} as of {self.as_of}"
            ) from exc

    def bond_issuers(self) -> list[str]:
        """Issuer codes with a registered bond curve."""
        return sorted(self._bond_curves.keys())

    def set_swap_curve(
        self,
        key: SwapCurveKey,
        curve: ql.YieldTermStructure | ql.YieldTermStructureHandle,
    ) -> None:
        """Register a swap curve (reserved for future use)."""
        if isinstance(curve, ql.YieldTermStructureHandle):
            self._swap_curves[key] = curve
        else:
            self._swap_curves[key] = ql.YieldTermStructureHandle(curve)

    def swap_curve(self, key: SwapCurveKey) -> ql.YieldTermStructureHandle:
        """Return a swap curve handle.

        Raises
        ------
        NotImplementedError
            When swap-curve construction is not yet wired up and *key* is absent.
        KeyError
            When no curve has been registered for *key*.
        """
        try:
            return self._swap_curves[key]
        except KeyError as exc:
            raise NotImplementedError(
                f"Swap curves are not implemented yet (requested {key!r} "
                f"as of {self.as_of})"
            ) from exc

    def swap_curve_keys(self) -> list[SwapCurveKey]:
        """Registered swap curve keys."""
        return list(self._swap_curves.keys())


@dataclass
class MarketContext:
    """Aggregated market data: curve collections and FX cross tables.

    Multiple labelled instances are supported (e.g. spot vs forward FX, or
    alternative curve sets).  Use ``"default"`` as the conventional label when
    only one instance of each type is present.
    """

    curve_collections: dict[str, CurveCollection] = field(default_factory=dict)
    fx_rates: dict[str, FXC] = field(default_factory=dict)

    def add_curve_collection(
        self,
        collection: CurveCollection,
        label: str = "default",
    ) -> None:
        self.curve_collections[label] = collection

    def curve_collection(self, label: str = "default") -> CurveCollection:
        try:
            return self.curve_collections[label]
        except KeyError as exc:
            raise KeyError(
                f"No curve collection labelled {label!r}"
            ) from exc

    def add_fxc(self, fxc: FXC, label: str = "default") -> None:
        self.fx_rates[label] = fxc

    def fxc(self, label: str = "default") -> FXC:
        try:
            return self.fx_rates[label]
        except KeyError as exc:
            raise KeyError(f"No FX table labelled {label!r}") from exc

    def curve_collection_labels(self) -> list[str]:
        return sorted(self.curve_collections.keys())

    def fxc_labels(self) -> list[str]:
        return sorted(self.fx_rates.keys())

