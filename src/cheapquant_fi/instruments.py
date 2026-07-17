"""Fixed-income instrument types."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Mapping

from cheapquant_fi.issuers import ISSUERS

# Column names from semantics/bond_analytics.yaml (bond_universe).
BOND_UNIVERSE_FIELDS: tuple[str, ...] = (
    "bond_id",
    "user_friendly_id",
    "issuer",
    "currency",
    "coupon",
    "maturity",
    "issue_date",
    "first_coupon_date",
    "accrual_start_date",
    "closest_tenor_pillar",
    "issue_amount",
)


def _parse_optional_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NULL", "NONE"}:
        return None
    return float(text)


def _parse_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _validate_bond_identifiers(
    bond_id: str | None, user_friendly_id: str | None
) -> tuple[str | None, str | None]:
    """Require at least one of ``bond_id`` or ``user_friendly_id``."""
    bond_id = _normalize_optional_str(bond_id)
    user_friendly_id = _normalize_optional_str(user_friendly_id)
    if bond_id is None and user_friendly_id is None:
        raise ValueError(
            "Bond requires at least one of bond_id or user_friendly_id"
        )
    return bond_id, user_friendly_id


def _validate_issuer_code(issuer: str) -> str:
    """Return a normalized issuer code that must be a key of :data:`ISSUERS`."""
    code = issuer.strip().upper()
    if code not in ISSUERS:
        supported = ", ".join(sorted(ISSUERS))
        raise ValueError(
            f"Unknown issuer {issuer!r}; expected one of ISSUERS keys: {supported}"
        )
    return code


@dataclass(frozen=True)
class Bond:
    """A bond as stored in the ``bond_universe`` table.

    Field names and types mirror ``semantics/bond_analytics.yaml``.  Date
    columns are represented as :class:`datetime.date` in Python; nullable
    columns use ``None`` when absent in the database.
    """

    issuer: str
    maturity: date
    bond_id: str | None = None
    user_friendly_id: str | None = None
    currency: str | None = None
    coupon: float | None = None
    issue_date: date | None = None
    first_coupon_date: date | None = None
    accrual_start_date: date | None = None
    closest_tenor_pillar: str | None = None
    issue_amount: float | None = None

    def __post_init__(self) -> None:
        bond_id, user_friendly_id = _validate_bond_identifiers(
            self.bond_id, self.user_friendly_id
        )
        object.__setattr__(self, "bond_id", bond_id)
        object.__setattr__(self, "user_friendly_id", user_friendly_id)
        object.__setattr__(self, "issuer", _validate_issuer_code(self.issuer))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Bond:
        """Build a :class:`Bond` from a DB row or dict keyed by column name."""
        maturity = _parse_optional_date(row.get("maturity"))
        if maturity is None:
            raise ValueError("bond_universe row is missing maturity")

        issuer = _parse_optional_str(row.get("issuer"))
        if not issuer:
            raise ValueError("bond_universe row is missing issuer")

        return cls(
            issuer=issuer,
            maturity=maturity,
            bond_id=_parse_optional_str(row.get("bond_id")),
            user_friendly_id=_parse_optional_str(row.get("user_friendly_id")),
            currency=_parse_optional_str(row.get("currency")),
            coupon=_parse_optional_float(row.get("coupon")),
            issue_date=_parse_optional_date(row.get("issue_date")),
            first_coupon_date=_parse_optional_date(row.get("first_coupon_date")),
            accrual_start_date=_parse_optional_date(row.get("accrual_start_date")),
            closest_tenor_pillar=_parse_optional_str(row.get("closest_tenor_pillar")),
            issue_amount=_parse_optional_float(row.get("issue_amount")),
        )

    def as_dict(self) -> dict[str, str | float | None]:
        """Serialize to bond_universe column values (dates as ISO strings)."""

        def _date_str(value: date | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "bond_id": self.bond_id,
            "user_friendly_id": self.user_friendly_id,
            "issuer": self.issuer,
            "currency": self.currency,
            "coupon": self.coupon,
            "maturity": _date_str(self.maturity),
            "issue_date": _date_str(self.issue_date),
            "first_coupon_date": _date_str(self.first_coupon_date),
            "accrual_start_date": _date_str(self.accrual_start_date),
            "closest_tenor_pillar": self.closest_tenor_pillar,
            "issue_amount": self.issue_amount,
        }

    def field_names(self) -> tuple[str, ...]:
        """Return dataclass field names in declaration order."""
        return tuple(field.name for field in fields(self))
