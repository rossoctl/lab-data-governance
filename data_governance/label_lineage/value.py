"""Reading a finding's value: what the classifier pointed at, as the text writes it.

The classifier marks *where* a value is, not always its extent: on a stored record
it marks ``123`` of ``"ssn": "123-45-6789"`` (and the ``6789`` of the suffix field
separately). Looking for a bare ``123`` downstream would match any ``123`` in any
payload, so exact matching is only precise once the value's extent is read from the
text itself. That is this module's one job.

- **Numeric types** (:data:`NUMERIC_TYPES`): the extent is the finding widened over
  the run of digits it sits in, a single separator (:data:`SEPARATORS`) allowed
  between two digits, a leading ``+`` kept. Quotes and other punctuation the
  classifier included at the edges are trimmed; a finding that is only the ``+``
  opening a number reads as that number. A run of more than
  :data:`MAX_DIGITS` digits is two numbers written together, so the finding keeps
  its own span. A numeric finding with no digit at all has no value.
- **Text types**: the extent is the finding, less any quote character or
  whitespace the classifier included at its edges (``"Alice"`` is ``Alice``;
  ``O'Brien`` stays ``O'Brien``).

The canonical form is the extent's bytes, unchanged: exact match is byte equality.
Reading per-type canonicals (digits only, case folds) is a structural
transformation and belongs to a later finder, not here.
"""

from __future__ import annotations

import dataclasses

#: NER tags whose value is a number written with optional separators.
NUMERIC_TYPES: frozenset[str] = frozenset({
    "AGE", "BAN", "CN", "CVV", "DATE", "DOB", "DOD", "EIN", "HICN", "HIP", "IMEI", "IP",
    "ITIN", "LMAN", "MED", "MRN", "NDC", "NID", "NPI", "PCED", "PCLD", "PCN", "PHONE", "RN",
    "SSN", "TAID",
})

#: Quote characters a text finding may carry at its edges.
QUOTES: frozenset[str] = frozenset("\"'`\u2018\u2019\u201c\u201d")

#: Characters allowed between two digits of one number.
SEPARATORS: frozenset[str] = frozenset("- ./")

#: More digits than this in one run is several numbers, not one (a card number,
#: the longest identifier here, has at most 19).
MAX_DIGITS = 19


@dataclasses.dataclass(frozen=True, slots=True)
class Value:
    """What a finding denotes: its ``canonical`` form and the extent ``[start, end)``
    of that form in the payload's projected text. ``canonical`` is empty when the
    finding holds no value (a numeric finding without a digit)."""

    canonical: str
    start: int
    end: int


def read_value(entity_type: str, text: str, start: int, end: int) -> Value:
    """The value of the finding ``text[start:end]`` of type *entity_type*."""
    if entity_type not in NUMERIC_TYPES:
        lo, hi = start, end
        while lo < hi and (text[lo].isspace() or text[lo] in QUOTES):
            lo += 1
        while hi > lo and (text[hi - 1].isspace() or text[hi - 1] in QUOTES):
            hi -= 1
        return Value(text[lo:hi], lo, hi) if lo < hi else Value("", start, end)
    lo, hi = _trim(text, start, end)
    if not any(c.isdigit() for c in text[lo:hi]):
        if not (end > start and text[end - 1] == "+" and _digit(text, end)):
            return Value("", start, end)
        # the classifier marked only the ``+`` (or a quote and the ``+``) that opens
        # a number: the value is the number it opens
        wide_lo, wide_hi = _widen(text, end, end + 1)
        if sum(c.isdigit() for c in text[wide_lo:wide_hi]) > MAX_DIGITS:
            return Value("", start, end)
        return Value(text[wide_lo:wide_hi], wide_lo, wide_hi)
    wide_lo, wide_hi = _widen(text, lo, hi)
    if sum(c.isdigit() for c in text[wide_lo:wide_hi]) <= MAX_DIGITS:
        lo, hi = wide_lo, wide_hi
    return Value(text[lo:hi], lo, hi)


def find_all(needle: str, text: str, *, bounded: bool = True) -> list[tuple[int, int]]:
    """Every ``[i, j)`` where *needle* occurs in *text* byte for byte; with
    *bounded*, only where it is not glued to a letter or a digit on either side
    (``alice`` is not in ``malice``, ``123`` is not in ``0123``). An empty needle
    occurs nowhere."""
    if not needle:
        return []
    spans = []
    at = text.find(needle)
    while at >= 0:
        end = at + len(needle)
        if not bounded or ((at == 0 or not text[at - 1].isalnum())
                           and (end == len(text) or not text[end].isalnum())):
            spans.append((at, end))
        at = text.find(needle, at + 1)
    return spans


def is_whole_value(entity_type: str, text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` is a whole value as :func:`read_value` reads it
    there: a number found inside a longer number (``123-45-6789`` as the tail of
    ``7788-123-45-6789`` or the head of ``123-45-6789-01``) is not. Text types are
    whole wherever they are bounded."""
    if entity_type not in NUMERIC_TYPES:
        return True
    v = read_value(entity_type, text, start, end)
    return (v.start, v.end) == (start, end)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop punctuation the classifier included at the edges (a quote, an escape);
    a leading ``+`` stays, it belongs to the number."""
    while start < end and not (text[start].isalnum() or text[start] == "+"):
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return start, end


def _digit(text: str, i: int) -> bool:
    return 0 <= i < len(text) and text[i].isdigit()


def _widen(text: str, lo: int, hi: int) -> tuple[int, int]:
    """Extend ``[lo, hi)`` over the digit run it sits in: digits, and one separator
    at a time when a digit follows it in the same direction."""
    if _digit(text, lo):
        while lo > 0:
            if _digit(text, lo - 1):
                lo -= 1
            elif text[lo - 1] in SEPARATORS and _digit(text, lo - 2):
                lo -= 2
            else:
                break
        if lo > 0 and text[lo - 1] == "+":
            lo -= 1
    if _digit(text, hi - 1):
        while hi < len(text):
            if _digit(text, hi):
                hi += 1
            elif text[hi] in SEPARATORS and _digit(text, hi + 1):
                hi += 2
            else:
                break
    return lo, hi
