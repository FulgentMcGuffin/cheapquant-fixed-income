"""Forward-starting tenors built from a delay plus a forward period."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from cheapquant_fi.issuers import IssuerProfile, resolve_issuer
from cheapquant_fi.tenor import Tenor, _TENOR_TOKEN

_TOKEN = _TENOR_TOKEN


def _token_unit(token: str) -> str:
    match = _TOKEN.fullmatch(token)
    if match is None:
        raise ValueError(f"Invalid tenor token {token!r}")
    unit = match.group(2)
    if unit not in ("`", "``"):
        unit = unit.lower()
    return unit


def _tokenize_combined_tenor(text: str) -> list[str]:
    """Split a combined tenor string into token substrings (same lexer as :class:`Tenor`)."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("Combined tenor string must not be empty")

    tokens: list[str] = []
    pos = 0
    for match in _TOKEN.finditer(stripped):
        if match.start() != pos:
            raise ValueError(
                f"Invalid combined tenor string {text!r} near {stripped[pos:]!r}"
            )
        tokens.append(match.group(0))
        pos = match.end()

    if pos != len(stripped):
        raise ValueError(f"Invalid combined tenor string {text!r} near {stripped[pos:]!r}")
    if not tokens:
        raise ValueError(f"Combined tenor string must contain at least one component: {text!r}")
    return tokens


def split_combined_tenor(text: str) -> tuple[str, str]:
    """Split a combined forward tenor into ``(starting_tenor, forward_tenor)`` substrings.

    Rules (see class docstring / README):

    - 1 token → ``("", forward)`` (immediate start)
    - 2 tokens → first / second
    - 3+ tokens with a repeated unit → split before the first repeated unit
    - 3+ tokens with no repeated unit → last token is forward, rest is start
    """
    tokens = _tokenize_combined_tenor(text)
    if len(tokens) == 1:
        return "", tokens[0]
    if len(tokens) == 2:
        return tokens[0], tokens[1]

    seen_units: set[str] = set()
    for index, token in enumerate(tokens):
        unit = _token_unit(token)
        if unit in seen_units:
            return "".join(tokens[:index]), "".join(tokens[index:])
        seen_units.add(unit)

    return "".join(tokens[:-1]), tokens[-1]


def _coerce_starting_tenor(value: Tenor | str) -> Tenor:
    if isinstance(value, Tenor):
        return value
    if not value:
        return Tenor()
    return Tenor.parse(value)


def _coerce_issuer(value: IssuerProfile | str) -> IssuerProfile:
    return resolve_issuer(value) if isinstance(value, str) else value


def _coerce_tenor(value: Tenor | str) -> Tenor:
    return Tenor.parse(value) if isinstance(value, str) else value


def _issuer_label(issuer: IssuerProfile) -> str:
    """Short lowercase issuer prefix used in :meth:`CompositeTenor.__str__`."""
    return issuer.source_code.lower()


@dataclass(frozen=True, init=False)
class CompositeTenor:
    """A forward-starting period: *starting_tenor* from an anchor, then *forward_tenor*.

    Example ``fra10y2y`` for France denotes a window starting roughly ten years
    from a supplied date and ending roughly twelve years from that date.

    Construct with separate tenors (:meth:`from_strings`) or a combined tenor
    string (:meth:`from_combined_tenor`), e.g. ``"10y12y"`` → 10y start + 12y forward.
    """

    issuer_profile: IssuerProfile
    starting_tenor: Tenor
    forward_tenor: Tenor

    def __new__(
        cls,
        issuer_profile: IssuerProfile | str,
        starting_tenor: Tenor | str,
        forward_tenor: Tenor | str,
    ) -> CompositeTenor:
        issuer = _coerce_issuer(issuer_profile)
        start = _coerce_starting_tenor(starting_tenor)
        forward = _coerce_tenor(forward_tenor)
        obj = object.__new__(cls)
        object.__setattr__(obj, "issuer_profile", issuer)
        object.__setattr__(obj, "starting_tenor", start)
        object.__setattr__(obj, "forward_tenor", forward)
        return obj

    @classmethod
    def from_strings(
        cls,
        issuer: str,
        starting_tenor: str,
        forward_tenor: str,
    ) -> CompositeTenor:
        """Build from issuer and separate starting / forward tenor strings."""
        return cls(issuer, starting_tenor, forward_tenor)

    @classmethod
    def from_combined_tenor(cls, issuer: str, combined_tenor: str) -> CompositeTenor:
        """Build from issuer and one combined tenor string (see :func:`split_combined_tenor`)."""
        starting, forward = split_combined_tenor(combined_tenor)
        return cls(issuer, starting, forward)

    def forward_start_date(self, when: date | datetime) -> date | datetime:
        """Anchor advanced by *starting_tenor* on the issuer calendar."""
        return self.starting_tenor.add_to(when, self.issuer_profile)

    def forward_end_date(self, when: date | datetime) -> date | datetime:
        """Forward start date advanced by *forward_tenor* on the issuer calendar."""
        start = self.forward_start_date(when)
        return self.forward_tenor.add_to(start, self.issuer_profile)

    def __str__(self) -> str:
        return (
            f"{_issuer_label(self.issuer_profile)}"
            f"{self.starting_tenor}"
            f"{self.forward_tenor}"
        )
