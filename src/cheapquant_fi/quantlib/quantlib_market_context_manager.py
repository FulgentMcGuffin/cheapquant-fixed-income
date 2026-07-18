"""Singleton registry for :class:`QuantlibMarketContext` instances by as_of date."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext


def _as_of_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


class QuantlibMarketContextManager:
    """Process-wide registry of :class:`QuantlibMarketContext` by valuation date.

    Use :meth:`instance` to access the singleton.  Contexts are registered
    automatically when :class:`QuantlibMarketContext` instances are created
    (via :func:`auto_register_market_context`).  A second context with the
    same ``as_of`` is merged into the stored instance; overlapping curve
    collection and FX labels are combined with ``|``.
    """

    _instance: QuantlibMarketContextManager | None = None

    def __new__(cls) -> QuantlibMarketContextManager:
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._contexts: dict[date, QuantlibMarketContext] = {}
            cls._instance = obj
        return cls._instance

    @classmethod
    def instance(cls) -> QuantlibMarketContextManager:
        """Return the singleton manager."""
        return cls()

    def get(self, as_of: date | datetime) -> QuantlibMarketContext | None:
        """Return the registered context for *as_of*, if any."""
        return self._contexts.get(_as_of_date(as_of))

    def require(self, as_of: date | datetime) -> QuantlibMarketContext:
        """Return the registered context for *as_of* or raise ``KeyError``."""
        key = _as_of_date(as_of)
        try:
            return self._contexts[key]
        except KeyError as exc:
            raise KeyError(
                f"No QuantlibMarketContext registered for {key.isoformat()}"
            ) from exc

    def register(self, context: QuantlibMarketContext) -> QuantlibMarketContext:
        """Register *context*, merging into an existing entry when needed.

        Returns the canonical stored instance (which may be *context* itself or
        an earlier registration for the same ``as_of``).
        """
        from cheapquant_fi.quantlib.quantlib_market_context import QuantlibMarketContext

        if not isinstance(context, QuantlibMarketContext):
            raise TypeError(f"Expected QuantlibMarketContext, got {type(context)!r}")

        if context.as_of is None:
            return context

        key = _as_of_date(context.as_of)
        existing = self._contexts.get(key)
        if existing is None:
            self._contexts[key] = context
            return context
        if existing is context:
            return context

        self._merge_into(existing, context)
        return existing

    def clear(self) -> None:
        """Remove all registered contexts (intended for tests)."""
        self._contexts.clear()

    @staticmethod
    def _merge_into(
        target: QuantlibMarketContext,
        source: QuantlibMarketContext,
    ) -> None:
        for label, collection in source.curve_collections.items():
            if label in target.curve_collections:
                target.curve_collections[label] = (
                    target.curve_collections[label] | collection
                )
            else:
                target.curve_collections[label] = collection

        for label, fxc in source.fx_rates.items():
            if label in target.fx_rates:
                target.fx_rates[label] = target.fx_rates[label] | fxc
            else:
                target.fx_rates[label] = fxc


def auto_register_market_context(cls: type) -> type:
    """Class decorator: register instances with :class:`QuantlibMarketContextManager`.

    Registration runs after ``__init__`` and after :meth:`set_curve_collection`
    / :meth:`set_fxc`, so contexts created without an initial ``as_of`` are
    indexed once their valuation date is established.
    """
    original_init = cls.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        QuantlibMarketContextManager.instance().register(self)

    cls.__init__ = __init__  # type: ignore[method-assign]

    for method_name in ("set_curve_collection", "set_fxc"):
        original = getattr(cls, method_name)

        def make_wrapper(method):
            def wrapped(self, *args, **kwargs):
                result = method(self, *args, **kwargs)
                QuantlibMarketContextManager.instance().register(self)
                return result

            return wrapped

        setattr(cls, method_name, make_wrapper(original))

    return cls
