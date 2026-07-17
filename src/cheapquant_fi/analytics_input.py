"""Input request types for fixed-income analytics calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cheapquant_fi.issuers import ISSUERS
from cheapquant_fi.instruments import Bond


@dataclass(frozen=True)
class BondAnalyticsInput:
    """Inputs required to price a fixed-coupon government bond."""

    issuer: str
    coupon: float
    maturity_date: date
    settlement_date: date
    trade_date: date | None = None
    issue_date: date | None = None
    face_amount: float = 100.0
    input_column: str | None = None
    input_value: float | None = None

    @classmethod
    def from_bond(
        cls,
        bond: Bond,
        *,
        settlement_date: date | None = None,
        trade_date: date | None = None,
        issue_date: date | None = None,
        face_amount: float | None = None,
        input_column: str | None = None,
        input_value: float | None = None,
    ) -> BondAnalyticsInput:
        """Build analytics inputs from a :class:`Bond` universe record."""
        if settlement_date is None:
            if trade_date is None:
                raise ValueError(
                    "trade_date is required when settlement_date is not provided"
                )
            settlement_date = ISSUERS[bond.issuer].settlement_date(trade_date)

        resolved_face_amount = (
            face_amount
            if face_amount is not None
            else bond.issue_amount if bond.issue_amount is not None else 100.0
        )

        return cls(
            issuer=bond.issuer,
            coupon=0.0 if bond.coupon is None else bond.coupon,
            maturity_date=bond.maturity,
            settlement_date=settlement_date,
            trade_date=trade_date,
            issue_date=issue_date if issue_date is not None else bond.issue_date,
            face_amount=resolved_face_amount,
            input_column=input_column,
            input_value=input_value,
        )


@dataclass(frozen=True)
class CmtAnalyticsInput:
    """Inputs required to price a synthetic constant-maturity treasury.

    When *coupon* is ``None`` the CMT is zero-coupon (default).  Set *coupon*
    to a percentage (e.g. ``2.5`` for 2.5 %) to price a fixed-coupon CMT with
    the issuer's payment frequency.
    """

    issuer: str
    tenor_label: str
    settlement_date: date
    trade_date: date | None = None
    coupon: float | None = None
    input_column: str | None = None
    input_value: float | None = None
