"""Tests for bond construction and issuer conventions."""

from __future__ import annotations

import QuantLib as ql

from cheapquant_fi.issuers import ISSUERS


def test_make_fixed_rate_bond_gbr():
    """GBR.make_fixed_rate_bond should produce a bond with negative accrued in ex-div window.

    A real gilt issued 31 Jul 2024 matures 31 Jul 2034 with semi-annual coupons
    landing on 31 Jan and 31 Jul each year.  The ex-dividend date for the
    31 Jan 2025 coupon is 7 business days before = 22 Jan 2025.  A settlement
    of 27 Jan 2025 (after that ex-div date) should yield negative accrued.
    """
    gbr = ISSUERS["GBR"]
    calendar = gbr.calendar()
    valuation = ql.Date(24, 1, 2025)
    ql.Settings.instance().evaluationDate = valuation

    issue_date = ql.Date(31, 7, 2024)
    maturity = ql.Date(31, 7, 2034)
    # Unadjusted convention keeps coupon dates exactly on 31 Jan / 31 Jul.
    schedule = ql.Schedule(
        issue_date,
        maturity,
        ql.Period(gbr.frequency),
        calendar,
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    bond = gbr.make_QL_fixed_rate_bond(schedule, [0.04], issue_date=issue_date)
    assert bond is not None

    # Settlement 27 Jan 2025 is after the ex-div date (22 Jan 2025),
    # so accrued interest should be negative (buyer pays rebate to seller).
    ex_div_settlement = ql.Date(27, 1, 2025)
    accrued = bond.accruedAmount(ex_div_settlement)
    assert accrued < 0, f"Expected negative accrued in ex-div window, got {accrued}"

    # One business day before the ex-div date (21 Jan 2025) is still cum-dividend:
    # accrued should be positive.
    # Note: QuantLib treats the ex-div date itself (22 Jan) as the FIRST ex-dividend
    # settlement day (settlement >= exDate is ex-coupon), consistent with the DMO
    # definition that "22 Jan is the ex-dividend date" from which the ex-period begins.
    cum_settlement = ql.Date(21, 1, 2025)
    assert bond.accruedAmount(cum_settlement) > 0
