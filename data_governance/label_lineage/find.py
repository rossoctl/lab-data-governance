"""The matcher registry: which occurrences of an input's units a payload holds.

A :data:`Finder` takes the occurrences at one input payload and one output payload
and returns the occurrences it finds at the output, each remembering the input
occurrence it came from (``source``). The fold never looks inside a finder.

``LABEL_MATCHER`` names the finder; an unknown name raises :class:`UnknownFinder`
rather than quietly falling back. Registered:

- ``exact`` (the default) — the held run of the value found byte for byte in the
  output, bounded (not glued to a letter or digit on either side) and, for a
  numeric type, the whole number at that place (the value read there with the
  same extent rule as at the origin): ``whole``, score 1.0, one occurrence per
  place it is found. Nothing else: no case fold, no
  separator reading, no digit comparison. A separator change is a structural
  transformation and is a different finder's to read.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

from .units import Occurrence, ProjectedPayload
from .value import find_all, is_whole_value

Finder = Callable[[Sequence[Occurrence], ProjectedPayload], list[Occurrence]]

LABEL_MATCHER_ENV_VAR = "LABEL_MATCHER"
DEFAULT_FINDER = "exact"


class UnknownFinder(ValueError):
    """``LABEL_MATCHER`` names no registered finder."""


def exact(occurrences: Sequence[Occurrence], output: ProjectedPayload) -> list[Occurrence]:
    """Every bounded byte-identical place in *output* of each occurrence's held run
    that is a whole value there."""
    found = []
    for occ in occurrences:
        for start, end in find_all(occ.held, output.text):
            if not is_whole_value(occ.unit.entity_type, output.text, start, end):
                continue
            found.append(Occurrence(
                unit=occ.unit, at=output.site, span=(start, end), held_from=occ.held_from,
                held_to=occ.held_to, match_kind="whole", score=1.0,
                matched_text=output.text[start:end], source=occ,
            ))
    return found


FINDERS: dict[str, Finder] = {"exact": exact}


def configured_name() -> str:
    """The finder ``LABEL_MATCHER`` names (``exact`` when unset)."""
    return os.environ.get(LABEL_MATCHER_ENV_VAR, "").strip().lower() or DEFAULT_FINDER


def get_finder(name: str | None = None) -> Finder:
    """The finder registered as *name* (default: the configured one)."""
    name = configured_name() if name is None else name
    try:
        return FINDERS[name]
    except KeyError:
        raise UnknownFinder(
            f"{LABEL_MATCHER_ENV_VAR}={name!r} names no finder; registered: {', '.join(sorted(FINDERS))}"
        ) from None
