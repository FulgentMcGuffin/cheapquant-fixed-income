"""Deliverable bond baskets for bond future contracts.

A :class:`DeliveryBasket` fixes *which* bonds may be delivered into a contract.
Membership is determined once, by the delivery month, and does not change —
analytics may later be run against the basket on any trade date.

Baskets can be built automatically from ``bond_universe`` or assembled by hand
from bond identifiers, optionally with hard-coded conversion factors.  Named
baskets live in the process-wide :class:`DeliveryBasketManager`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from cqfi.bond_futures import (
    BondFuture,
    BondFutureError,
    resolve_bond_future_convention,
    resolve_delivery_month,
)
from cqfi.bond_manager import BondManager
from cqfi.date_utils import whole_months_between
from cqfi.instruments import Bond
from cqfi.numeric_term_structure import NumericTermStructure

# "<bond id>" or "<bond id>|<conversion factor>"
_BOND_SPEC_RE = re.compile(r"^(?P<id>[^|\s]+)(?:\|(?P<cf>[0-9]*\.?[0-9]+))?$")
_DLV_RE = re.compile(
    r"^/dlv(?:\s+(?P<rest>.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_FUT_RE = re.compile(
    r"^/fut(?:\s+(?P<rest>.*))?$",
    re.IGNORECASE | re.DOTALL,
)
# "<target> [trade_date] [repo]" for /fut, where repo is a bare number (a
# flat repo rate) or a JSON object mapping tenor labels to rates in percent.
_FUT_ARGS_RE = re.compile(
    r"^(?P<target>\S+)"
    r"(?:\s+(?P<trade_date>\d{4}-\d{2}-\d{2}))?"
    r"(?:\s+(?P<repo>-?[0-9]*\.?[0-9]+|\{.*\}))?\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Tokens that resolve_delivery_month understands, used to tell a delivery
# specifier apart from a bond identifier in a /dlv argument list.
_DELIVERY_TOKEN_RE = re.compile(
    r"^(?:\d{4}-\d{2}|[FGHJKMNQUVXZ]\d{0,4}|\d)$", re.IGNORECASE
)


class DeliveryBasketError(ValueError):
    """Raised when a basket cannot be built or a bond is not deliverable."""


@dataclass(frozen=True)
class BasketMember:
    """One deliverable bond, with optional per-bond overrides.

    Attributes:
        bond: The deliverable bond.
        conversion_factor_override: When set, used verbatim instead of being
            computed from the contract terms.
        repo_term_structure_override: When set, used to finance *this* bond
            to delivery instead of the basket-wide term structure supplied on
            :class:`~cqfi.bond_future_input.BondFutureInput`. See
            :meth:`DeliveryBasket.repo_term_structure_for`.
    """

    bond: Bond
    conversion_factor_override: float | None = None
    repo_term_structure_override: NumericTermStructure | None = None

    @property
    def identifier(self) -> str:
        """Return the bond's preferred identifier."""
        return self.bond.user_friendly_id or self.bond.bond_id or ""


@dataclass
class DeliveryBasket:
    """The set of bonds deliverable into one dated bond future contract."""

    bond_future: BondFuture
    members: tuple[BasketMember, ...] = ()
    name: str | None = None

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def auto(
        cls,
        bond_future: BondFuture,
        *,
        name: str | None = None,
        db_path: str | Path | None = None,
    ) -> DeliveryBasket:
        """Build the basket from every eligible bond in ``bond_universe``.

        Args:
            bond_future: The dated contract to build the basket for.
            name: Optional basket name.
            db_path: Override for the bond analytics database.

        Returns:
            A basket holding every bond that passes the contract's
            restrictions as of the delivery date.
        """
        basket = cls(bond_future=bond_future, name=name)
        delivery_date = bond_future.delivery_end_date()
        window = bond_future.convention.restrictions.remaining_maturity
        earliest, latest = window.bounds(delivery_date)

        candidates = BondManager.instance().get_by_issuer(
            bond_future.convention.issuer_code,
            maturity_from=earliest,
            maturity_to=latest,
            db_path=db_path,
        )
        for bond in candidates:
            admitted, _ = bond_future.convention.restrictions.admits(
                bond, delivery_date
            )
            if admitted:
                basket.add(bond)
        return basket

    @classmethod
    def from_bond_ids(
        cls,
        bond_future: BondFuture,
        ids_with_cf: list[tuple[str, float | None]],
        *,
        name: str | None = None,
        db_path: str | Path | None = None,
    ) -> DeliveryBasket:
        """Build a basket from explicit identifiers and conversion factors.

        Args:
            bond_future: The dated contract to build the basket for.
            ids_with_cf: ``(identifier, conversion_factor_or_None)`` pairs.
            name: Optional basket name.
            db_path: Override for the bond analytics database.

        Returns:
            A basket holding exactly the requested bonds.

        Raises:
            DeliveryBasketError: If an identifier is unknown or its bond is
                not deliverable into the contract.
        """
        basket = cls(bond_future=bond_future, name=name)
        manager = BondManager.instance()
        for identifier, conversion_factor in ids_with_cf:
            bond = manager.get(identifier, db_path=db_path)
            if bond is None:
                raise DeliveryBasketError(f"Unknown bond identifier {identifier!r}")
            basket.add(bond, conversion_factor)
        return basket

    # ------------------------------------------------------------------ #
    # Membership
    # ------------------------------------------------------------------ #
    def add(
        self,
        bond: Bond,
        conversion_factor: float | None = None,
        repo_term_structure: NumericTermStructure | None = None,
    ) -> None:
        """Admit *bond* to the basket after checking every restriction.

        Args:
            bond: Candidate deliverable bond.
            conversion_factor: Hard-coded factor to use instead of computing one.
            repo_term_structure: Financing curve to use for *this* bond
                instead of the basket-wide term structure supplied on the
                analytics request (:attr:`BondFutureInput.repo_term_structure`).
                When ``None`` (the default), the basket-wide term structure —
                or, absent that, the discount curve's own forward rate —
                applies, exactly as before this option existed.

        Raises:
            DeliveryBasketError: If the bond's issuer differs from the
                contract's, if it fails a restriction, or if it is already in
                the basket.
        """
        convention = self.bond_future.convention
        if bond.issuer != convention.issuer_code:
            raise DeliveryBasketError(
                f"Bond {_identify(bond)} is issued by {bond.issuer}, but "
                f"{convention.name} delivers {convention.issuer_code} bonds"
            )

        admitted, reason = convention.restrictions.admits(
            bond, self.bond_future.delivery_end_date()
        )
        if not admitted:
            raise DeliveryBasketError(
                f"Bond {_identify(bond)} is not deliverable into "
                f"{self.bond_future}: {reason}"
            )

        if any(member.bond == bond for member in self.members):
            raise DeliveryBasketError(
                f"Bond {_identify(bond)} is already in the basket"
            )

        self.members = (
            *self.members,
            BasketMember(bond, conversion_factor, repo_term_structure),
        )

    def bonds(self) -> tuple[Bond, ...]:
        """Return the deliverable bonds in insertion order."""
        return tuple(member.bond for member in self.members)

    def override_for(self, bond: Bond) -> float | None:
        """Return the hard-coded conversion factor for *bond*, if any."""
        for member in self.members:
            if member.bond == bond:
                return member.conversion_factor_override
        return None

    def repo_term_structure_for(self, bond: Bond) -> NumericTermStructure | None:
        """Return the per-bond repo term structure override for *bond*, if any.

        ``None`` means *bond* finances at the basket-wide term structure
        supplied on the analytics request, or the discount curve's own
        forward rate when that is also unset.
        """
        for member in self.members:
            if member.bond == bond:
                return member.repo_term_structure_override
        return None

    def set_repo_term_structure(
        self, bond: Bond, repo_term_structure: NumericTermStructure | None
    ) -> None:
        """Set or clear the repo term structure override for an existing member.

        Useful for baskets built with :meth:`auto`, where per-bond financing
        curves cannot be supplied at construction time.

        Args:
            bond: An existing member of this basket.
            repo_term_structure: The override to apply, or ``None`` to clear
                it and fall back to the basket-wide term structure.

        Raises:
            DeliveryBasketError: If *bond* is not a member of this basket.
        """
        for index, member in enumerate(self.members):
            if member.bond == bond:
                self.members = (
                    *self.members[:index],
                    replace(member, repo_term_structure_override=repo_term_structure),
                    *self.members[index + 1 :],
                )
                return
        raise DeliveryBasketError(f"Bond {_identify(bond)} is not in the basket")

    def renamed(self, name: str) -> DeliveryBasket:
        """Return a copy of this basket under a different name."""
        return replace(self, name=name)

    def __len__(self) -> int:
        return len(self.members)

    # ------------------------------------------------------------------ #
    # Presentation
    # ------------------------------------------------------------------ #
    def to_polars(self) -> pl.DataFrame:
        """Return the deliverable bonds and their conversion factors."""
        delivery_date = self.bond_future.delivery_end_date()
        return pl.DataFrame(
            [
                {
                    "bond_id": member.bond.bond_id,
                    "user_friendly_id": member.bond.user_friendly_id,
                    "issuer": member.bond.issuer,
                    "coupon": member.bond.coupon,
                    "maturity": member.bond.maturity,
                    "remaining_months": whole_months_between(
                        delivery_date, member.bond.maturity
                    ),
                    "conversion_factor": member.conversion_factor_override,
                }
                for member in self.members
            ],
            schema={
                "bond_id": pl.String,
                "user_friendly_id": pl.String,
                "issuer": pl.String,
                "coupon": pl.Float64,
                "maturity": pl.Date,
                "remaining_months": pl.Int64,
                "conversion_factor": pl.Float64,
            },
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of the basket."""
        return {
            "name": self.name,
            "contract": str(self.bond_future),
            "delivery_date": self.bond_future.delivery_end_date().isoformat(),
            "bond_count": len(self.members),
            "bonds": [
                {
                    **member.bond.as_dict(),
                    "conversion_factor": member.conversion_factor_override,
                }
                for member in self.members
            ],
        }

    def as_json(self, **kwargs) -> str:
        """Return the basket as a JSON object string."""
        return json.dumps(self.as_dict(), **kwargs)


def _identify(bond: Bond) -> str:
    return bond.user_friendly_id or bond.bond_id or "<unidentified>"


class DeliveryBasketManager:
    """Process-wide registry of named delivery baskets.

    Mirrors :class:`~cqfi.bond_manager.BondManager`: a singleton keyed
    by name, populated by ``/dlv`` and read back by ``/fut``.  Baskets live for
    the life of the process only.
    """

    _instance: DeliveryBasketManager | None = None

    def __new__(cls) -> DeliveryBasketManager:
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._baskets: dict[str, DeliveryBasket] = {}
            cls._instance = obj
        return cls._instance

    @classmethod
    def instance(cls) -> DeliveryBasketManager:
        """Return the singleton manager."""
        return cls()

    def put(self, name: str, basket: DeliveryBasket) -> None:
        """Store *basket* under *name*, replacing any existing entry."""
        key = name.strip()
        if not key:
            raise DeliveryBasketError("basket name must not be empty")
        self._baskets[key] = basket.renamed(key)

    def get(self, name: str) -> DeliveryBasket | None:
        """Return the basket stored under *name*, or ``None``."""
        return self._baskets.get(name.strip())

    def names(self) -> list[str]:
        """Return the stored basket names, sorted."""
        return sorted(self._baskets)

    def clear(self) -> None:
        """Remove every stored basket (intended for tests)."""
        self._baskets.clear()


# --------------------------------------------------------------------------- #
# Command parsing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DlvParseResult:
    """Outcome of parsing a ``/dlv`` command.

    Attributes:
        kind: ``"help"`` for a bare command, ``"invalid"`` when malformed,
            ``"auto"`` to populate from ``bond_universe``, ``"explicit"`` when
            bond identifiers were supplied.
        name: Basket name to store under.
        future_code: Contract code, before delivery-month resolution.
        delivery_token: Raw delivery specifier, if one was given.
        bond_specs: ``(identifier, conversion_factor_or_None)`` pairs.
        message: Explanation, set for ``"invalid"``.
    """

    kind: Literal["help", "invalid", "auto", "explicit"]
    name: str | None = None
    future_code: str | None = None
    delivery_token: str | None = None
    bond_specs: tuple[tuple[str, float | None], ...] = ()
    message: str | None = None

    def build(
        self, *, today: date | None = None, db_path: str | Path | None = None
    ) -> DeliveryBasket:
        """Build the basket this result describes.

        Raises:
            DeliveryBasketError: If the result is not a buildable kind.
        """
        if self.kind not in ("auto", "explicit"):
            raise DeliveryBasketError(f"cannot build a {self.kind!r} parse result")

        convention = resolve_bond_future_convention(self.future_code)
        year, month = resolve_delivery_month(self.delivery_token, today=today)
        future = BondFuture(
            convention=convention, delivery_month=month, delivery_year=year
        )
        if self.kind == "auto":
            return DeliveryBasket.auto(future, name=self.name, db_path=db_path)
        return DeliveryBasket.from_bond_ids(
            future, list(self.bond_specs), name=self.name, db_path=db_path
        )


@dataclass(frozen=True)
class FutParseResult:
    """Outcome of parsing a ``/fut`` command.

    Attributes:
        kind: ``"help"``, ``"invalid"`` or ``"analytics"``.
        target: A stored basket name or a contract code such as ``IKH7``.
        trade_date: Trade date to run analytics on, if supplied.
        numeric_term_structure: Optional repo input, applied to every
            deliverable bond in the basket. A single number is a flat repo
            rate held for every tenor (i.e. for eternity); a ``dict`` maps
            tenor labels such as ``"3m"`` to rates in percent for a full
            curve.
        message: Explanation, set for ``"invalid"``.
    """

    kind: Literal["help", "invalid", "analytics"]
    target: str | None = None
    trade_date: date | None = None
    numeric_term_structure: dict[str, float] | float | None = None
    message: str | None = None


def _parse_bond_spec(token: str) -> tuple[str, float | None] | None:
    """Parse ``"fraapr029"`` or ``"fraapr029|1.0326"``."""
    match = _BOND_SPEC_RE.match(token)
    if match is None:
        return None
    conversion_factor = match.group("cf")
    return (
        match.group("id"),
        None if conversion_factor is None else float(conversion_factor),
    )


def _looks_like_delivery_token(token: str) -> bool:
    """Whether *token* is a delivery specifier rather than a bond identifier."""
    return _DELIVERY_TOKEN_RE.match(token) is not None


def parse_dlv_command(text: str, *, today: date | None = None) -> DlvParseResult | None:
    """Parse a ``/dlv`` command into a description of the basket to build.

    Supported forms::

        /dlv mybasket FGBM                       # front quarterly contract
        /dlv mybasket OE M8                      # June 2028
        /dlv mybasket FGBM U                     # next September
        /dlv mybasket FGBM 6                     # next quarterly, year ending 6
        /dlv hist FGBS 2020-09                   # historical
        /dlv hist FGBS U2020                     # historical
        /dlv mine FOA fraapr029 frajun030 2025-12
        /dlv mine FOA fraapr029|1.0326 frajun030|1.0291 2025-12

    Args:
        text: The raw command line.
        today: Reference date for relative delivery specifiers.

    Returns:
        A parse result, or ``None`` when *text* is not a ``/dlv`` command.
    """
    match = _DLV_RE.match(text.strip())
    if match is None:
        return None

    rest = (match.group("rest") or "").strip()
    if not rest:
        return DlvParseResult(kind="help")

    tokens = rest.split()
    if len(tokens) < 2:
        return DlvParseResult(
            kind="invalid",
            message="/dlv needs a basket name and a bond future code.",
        )

    name, future_code, *arguments = tokens

    try:
        resolve_bond_future_convention(future_code)
    except BondFutureError as exc:
        return DlvParseResult(kind="invalid", message=str(exc))

    # A trailing delivery specifier may follow any bond identifiers.
    delivery_token: str | None = None
    if arguments and _looks_like_delivery_token(arguments[-1]):
        delivery_token = arguments.pop()

    bond_specs: list[tuple[str, float | None]] = []
    for token in arguments:
        spec = _parse_bond_spec(token)
        if spec is None:
            return DlvParseResult(
                kind="invalid",
                message=(
                    f"Cannot read {token!r} as a bond identifier; expected "
                    "'<id>' or '<id>|<conversion factor>'."
                ),
            )
        bond_specs.append(spec)

    try:
        resolve_delivery_month(delivery_token, today=today)
    except BondFutureError as exc:
        return DlvParseResult(kind="invalid", message=str(exc))

    return DlvParseResult(
        kind="explicit" if bond_specs else "auto",
        name=name,
        future_code=future_code,
        delivery_token=delivery_token,
        bond_specs=tuple(bond_specs),
    )


def parse_fut_command(text: str) -> FutParseResult | None:
    """Parse a ``/fut`` command.

    Supported forms::

        /fut IKH7                            # latest available trade date
        /fut IKH7 2026-05-15                 # explicit trade date
        /fut mybasket 2025-10-15             # a basket stored earlier by /dlv
        /fut IKH7 3.0                        # flat 3% repo, latest trade date
        /fut IKH7 2026-05-15 3.0             # flat 3% repo on a trade date
        /fut IKH7 2026-05-15 {"3m": 3.0, "1y": 3.2}   # full repo curve

    A repo argument, when supplied, is applied to every deliverable bond in
    the basket. A bare number is a flat repo rate held for every tenor
    (i.e. for eternity); a JSON object maps tenor labels to rates in
    percent.

    Args:
        text: The raw command line.

    Returns:
        A parse result, or ``None`` when *text* is not a ``/fut`` command.
    """
    match = _FUT_RE.match(text.strip())
    if match is None:
        return None

    rest = (match.group("rest") or "").strip()
    if not rest:
        return FutParseResult(kind="help")

    args_match = _FUT_ARGS_RE.match(rest)
    if args_match is None:
        return FutParseResult(
            kind="invalid",
            message=(
                "/fut takes a basket name or contract code, an optional "
                "trade date (YYYY-MM-DD), and an optional repo rate or term "
                "structure (a number, or a JSON tenor:rate map)."
            ),
        )

    target = args_match.group("target")

    trade_date: date | None = None
    trade_date_str = args_match.group("trade_date")
    if trade_date_str:
        try:
            trade_date = date.fromisoformat(trade_date_str)
        except ValueError as exc:
            return FutParseResult(kind="invalid", message=str(exc))

    numeric_term_structure: dict[str, float] | float | None = None
    repo_str = args_match.group("repo")
    if repo_str:
        stripped_repo = repo_str.strip()
        try:
            if stripped_repo.startswith("{"):
                parsed_repo = eval(stripped_repo)  # noqa: S307 - trusted local CLI input
                if not isinstance(parsed_repo, dict):
                    raise ValueError("expected a JSON object of tenor:rate pairs")
                numeric_term_structure = parsed_repo
            else:
                numeric_term_structure = float(stripped_repo)
        except Exception as exc:
            return FutParseResult(
                kind="invalid",
                message=(
                    f"Cannot read {repo_str!r} as a repo rate or term "
                    f"structure: {exc}"
                ),
            )

    return FutParseResult(
        kind="analytics",
        target=target,
        trade_date=trade_date,
        numeric_term_structure=numeric_term_structure,
    )


def resolve_basket(
    target: str,
    *,
    today: date | None = None,
    db_path: str | Path | None = None,
) -> DeliveryBasket:
    """Return a stored basket by name, or build one from a contract code.

    A stored name always wins, so ``/fut mybasket`` keeps any hard-coded
    conversion factors that ``/dlv`` recorded.

    Args:
        target: A basket name or a bond future code such as ``IKH7``.
        today: Reference date for relative delivery specifiers.
        db_path: Override for the bond analytics database.

    Returns:
        The resolved basket.

    Raises:
        DeliveryBasketError: If *target* is neither a stored basket nor a
            recognisable contract code.
    """
    stored = DeliveryBasketManager.instance().get(target)
    if stored is not None:
        return stored

    try:
        future = BondFuture.parse(target, today=today)
    except BondFutureError as exc:
        raise DeliveryBasketError(
            f"{target!r} is neither a stored basket nor a bond future code. {exc}"
        ) from exc
    return DeliveryBasket.auto(future, db_path=db_path)


def extract_bonds(
    future_code: str,
    *,
    today: date | None = None,
    db_path: str | Path | None = None,
) -> tuple[Bond, ...]:
    """Return the deliverable bonds for a contract code such as ``FBTPU9``.

    Args:
        future_code: Contract code, with or without a delivery specifier.
        today: Reference date for relative delivery specifiers.
        db_path: Override for the bond analytics database.

    Returns:
        The deliverable bonds, ordered by maturity.
    """
    future = BondFuture.parse(future_code, today=today)
    return DeliveryBasket.auto(future, db_path=db_path).bonds()
