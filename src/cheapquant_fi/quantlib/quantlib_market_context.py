"""Market data containers for curves and FX used in QuantLib analytics.

Example
-------
Build a bond curve handle and register it on a :class:`QuantlibMarketContext`::

    from datetime import date

    import polars as pl

    from cheapquant_fi.issuers import resolve_issuer
    from cheapquant_fi.quantlib.quantlib_curve import ql_build_zero_curve
    from cheapquant_fi.quantlib.quantlib_market_context import (
        FXC,
        QuantLibCurveCollection,
        QuantlibMarketContext,
    )

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
    curve_handle, _ = ql_build_zero_curve(issuer, as_of, rates_df)

    curves = QuantLibCurveCollection(as_of=as_of)
    curves.set_bond_curve("USA", curve_handle)

    fx = FXC(as_of=as_of)
    fx.set_rate("AUD", "USD", 1.45)

    ctx = QuantlibMarketContext()
    ctx.add_curve_collection(curves)
    ctx.add_fxc(fx)

    # curve_handle is a ql.YieldTermStructureHandle; retrieve via issuer code:
    usa_curve = ctx.curve_collection().bond_curve("USA")
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import QuantLib as ql

from cheapquant_fi.config import get_settings
from cheapquant_fi.data.rates_loader import load_curve_rates
from cheapquant_fi.issuers import IssuerProfile, RateType, resolve_issuer
from cheapquant_fi.quantlib.quantlib_curve import (
    QLZeroInterp,
    ZeroCurveBuildOptions,
)


def _normalize_ccy(code: str) -> str:
    return code.strip().upper()


def _normalize_issuer(code: str) -> str:
    return code.strip().upper()


def _as_of_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


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

    def __or__(self, other: FXC) -> FXC:
        """Merge two FX tables with the same ``as_of`` date.

        Stored rate pairs are combined; *other* wins on duplicate keys.
        """
        if not isinstance(other, FXC):
            return NotImplemented
        if _as_of_date(self.as_of) != _as_of_date(other.as_of):
            raise ValueError(
                f"Cannot merge FX tables with different as_of dates: "
                f"{self.as_of!r} vs {other.as_of!r}"
            )
        return FXC(
            as_of=self.as_of,
            _rates={**self._rates, **other._rates},
        )

    def __ror__(self, other: FXC) -> FXC:
        if not isinstance(other, FXC):
            return NotImplemented
        return other | self


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
class QuantLibCurveCollection:
    """Yield curves as of a single valuation date or datetime.

    Bond curves are indexed by sovereign issuer code (``USA``, ``DEU``, …) as
    built by :func:`cheapquant_fi.quantlib.quantlib_curve.ql_build_zero_curve`.  Swap curves
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

    def __or__(self, other: QuantLibCurveCollection) -> QuantLibCurveCollection:
        """Merge two collections with the same ``as_of`` date.

        Bond and swap curve dicts are combined; *other* wins on duplicate keys.
        """
        if not isinstance(other, QuantLibCurveCollection):
            return NotImplemented
        if _as_of_date(self.as_of) != _as_of_date(other.as_of):
            raise ValueError(
                f"Cannot merge curve collections with different as_of dates: "
                f"{self.as_of!r} vs {other.as_of!r}"
            )
        return QuantLibCurveCollection(
            as_of=self.as_of,
            _bond_curves={**self._bond_curves, **other._bond_curves},
            _swap_curves={**self._swap_curves, **other._swap_curves},
        )

    def __ror__(self, other: QuantLibCurveCollection) -> QuantLibCurveCollection:
        if not isinstance(other, QuantLibCurveCollection):
            return NotImplemented
        return other | self


def ql_build_curve_collections(
    date_issuer_pairs: Iterable[tuple[date, IssuerProfile]],
    db_path: str | Path | None = None,
    *,
    rate_type: RateType = RateType.ZERO,
    interpolation: QLZeroInterp | None = None,
    bspline_knots: list[float] | None = None,
    poly_degree: int = 3,
    curve_options: ZeroCurveBuildOptions | None = None,
) -> list[QuantLibCurveCollection]:
    """Build one :class:`QuantLibCurveCollection` per valuation date.

    *date_issuer_pairs* must contain unique ``(date, issuer)`` combinations.
    Pairs sharing the same date are grouped into a single collection whose
    ``_bond_curves`` dict holds one entry per issuer.  The returned list is
    sorted by date ascending with no duplicate ``as_of`` values.

    Pillar rates are loaded from *db_path* via
    :func:`cheapquant_fi.data.rates_loader.load_curve_rates`.  When *db_path*
    is ``None``, ``paths.ycs_db`` from ``config/cqfi.yaml`` is used.

    Parameters
    ----------
    date_issuer_pairs:
        ``(valuation_date, issuer)`` pairs to build curves for.
    db_path:
        Path to the rates database.  Defaults to ``ycs_db`` from
        ``config/cqfi.yaml`` (respecting ``CQFI_CONFIG`` / ``CQFI_YCS_DB``).
    rate_type, interpolation, bspline_knots, poly_degree:
        Passed through to :func:`ql_build_zero_curve` unless *curve_options*
        is supplied (which takes precedence).
    curve_options:
        Optional bundled build options; overrides the individual keyword args
        when provided.

    Returns
    -------
    list[QuantLibCurveCollection]
        One collection per distinct valuation date, ascending.
    """
    if curve_options is None:
        curve_options = ZeroCurveBuildOptions(
            rate_type=rate_type,
            interpolation=interpolation,
            bspline_knots=bspline_knots,
            poly_degree=poly_degree,
        )

    resolved_db_path = db_path if db_path is not None else get_settings().ycs_db_path

    by_date: dict[date, list[IssuerProfile]] = defaultdict(list)
    seen: set[tuple[date, str]] = set()

    for val_date, issuer in date_issuer_pairs:
        key = (val_date, issuer.source_code)
        if key in seen:
            raise ValueError(
                f"Duplicate (date, issuer) pair: {val_date.isoformat()}, "
                f"{issuer.source_code!r}"
            )
        seen.add(key)
        by_date[val_date].append(issuer)

    collections: list[QuantLibCurveCollection] = []
    for val_date in sorted(by_date):
        collection = QuantLibCurveCollection(as_of=val_date)
        for issuer in by_date[val_date]:
            rates_df = load_curve_rates(
                resolved_db_path,
                issuer,
                val_date,
                rate_type=curve_options.rate_type,
            )
            curve_handle, _ = curve_options.build(issuer, val_date, rates_df)
            collection.set_bond_curve(issuer.source_code, curve_handle)
        collections.append(collection)

    return collections


_BOND_CURVE_LABELS: dict[RateType, str] = {
    RateType.ZERO: "BOND_ZERO",
    RateType.PAR: "BOND_PAR",
}


def ql_build_market_context(
    trade_date: date,
    issuers: list[str],
    db_path: str | Path | None = None,
    *,
    rate_type: RateType = RateType.ZERO,
    interpolation: QLZeroInterp | None = None,
    bspline_knots: list[float] | None = None,
    poly_degree: int = 3,
    curve_options: ZeroCurveBuildOptions | None = None,
) -> QuantlibMarketContext:
    """Build a :class:`QuantlibMarketContext` with bond curves for *trade_date*.

    Resolves each issuer name via :func:`cheapquant_fi.issuers.resolve_issuer`,
    builds curves with :func:`ql_build_curve_collections`, and registers the
    resulting collection under ``"BOND_ZERO"`` or ``"BOND_PAR"`` depending on
    the effective *rate_type*.

    Parameters
    ----------
    trade_date:
        Valuation date for all bond curves.
    issuers:
        Sovereign issuer codes (e.g. ``"USA"``, ``"DEU"``).
    db_path, rate_type, interpolation, bspline_knots, poly_degree, curve_options:
        Forwarded to :func:`ql_build_curve_collections` (except
        *date_issuer_pairs*, which is derived from *trade_date* and *issuers*).

    Returns
    -------
    QuantlibMarketContext
        Context with one labelled bond curve collection.
    """
    if not issuers:
        raise ValueError("At least one issuer is required")

    if curve_options is None:
        curve_options = ZeroCurveBuildOptions(
            rate_type=rate_type,
            interpolation=interpolation,
            bspline_knots=bspline_knots,
            poly_degree=poly_degree,
        )

    date_issuer_pairs = [
        (trade_date, resolve_issuer(issuer)) for issuer in issuers
    ]
    collections = ql_build_curve_collections(
        date_issuer_pairs,
        db_path,
        curve_options=curve_options,
    )
    if len(collections) != 1:
        raise RuntimeError(
            f"Expected one curve collection for {trade_date.isoformat()}, "
            f"got {len(collections)}"
        )

    label = _BOND_CURVE_LABELS[curve_options.rate_type]
    context = QuantlibMarketContext()
    context.set_curve_collection(collections[0], label=label)
    return context


@dataclass
class QuantlibMarketContext:
    """Aggregated market data: curve collections and FX cross tables.

    Multiple labelled instances are supported (e.g. spot vs forward FX, or
    alternative curve sets).  Use ``"default"`` as the conventional label when
    only one instance of each type is present.
    """

    as_of: date | None = None
    curve_collections: dict[str, QuantLibCurveCollection] = field(default_factory=dict)
    fx_rates: dict[str, FXC] = field(default_factory=dict)

    def set_curve_collection(
        self,
        collection: QuantLibCurveCollection,
        label: str = "default",
    ) -> None:
        if self.as_of is not None and collection.as_of != self.as_of:
            raise ValueError(f"Collection as_of {collection.as_of} does not match context as_of {self.as_of}")
        if self.as_of is None:
            self.as_of = collection.as_of        
        if label in self.curve_collections:
            raise ValueError(f"Curve collection labelled {label} already exists")
        self.curve_collections[label] = collection

    def curve_collection(self, label: str = "default") -> QuantLibCurveCollection:
        try:
            return self.curve_collections[label]
        except KeyError as exc:
            raise KeyError(
                f"No curve collection labelled {label!r}"
            ) from exc

    def set_fxc(self, fxc: FXC, label: str = "default") -> None:
        if label in self.fx_rates:
            raise ValueError(f"FX table labelled {label} already exists")
        if self.as_of is not None and fxc.as_of != self.as_of:
            raise ValueError(f"FX table as_of {fxc.as_of} does not match context as_of {self.as_of}")
        if self.as_of is None:
            self.as_of = fxc.as_of
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


from cheapquant_fi.quantlib.quantlib_market_context_manager import auto_register_market_context

auto_register_market_context(QuantlibMarketContext)
