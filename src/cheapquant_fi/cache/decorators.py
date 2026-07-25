"""Decorators that persist analytics method results into ``quant_cache_db``."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.analytics_output import FixedIncomeAnalyticsOutput
from cheapquant_fi.cache.registry import get_cache_registry
from cheapquant_fi.config import get_runtime_settings

F = TypeVar("F", bound=Callable[..., Any])

BondComputeResult = tuple[
    FixedIncomeAnalyticsOutput,
    FixedIncomeAnalyticsOutput | None,
    FixedIncomeAnalyticsOutput | None,
]


def cache_bond_analytics(fn: F) -> F:
    """Persist ``compute_bond_analytics`` outputs when ``use_quant_cache`` is true.

    Expects return shape ``(bond, mm_cmt | None, mm_fc_cmt | None)`` and a
    ``BondAnalyticsInput`` as the first positional arg after ``self``.
    """

    @functools.wraps(fn)
    def wrapper(
        self: Any,
        request: BondAnalyticsInput,
        market: Any = None,
        *args: Any,
        curve_label: str = "BOND_ZERO",
        **kwargs: Any,
    ) -> BondComputeResult:
        result: BondComputeResult = fn(
            self, request, market, *args, curve_label=curve_label, **kwargs
        )
        if not get_runtime_settings().use_quant_cache:
            return result

        bond_metrics, mm_cmt, mm_fc = result
        # Skip entirely empty payloads; still cache bond when present.
        if bond_metrics is None:
            return result

        registry = get_cache_registry()
        registry.persist_bond_compute(
            owner=type(self).__name__,
            method=fn.__name__,
            request=request,
            curve_label=curve_label,
            bond_metrics=bond_metrics,
            mm_cmt_metrics=mm_cmt,
            mm_fc_cmt_metrics=mm_fc,
        )
        return bond_metrics, mm_cmt, mm_fc

    return wrapper  # type: ignore[return-value]
