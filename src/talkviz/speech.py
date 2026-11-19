"""Check that the numbers in a spoken reply come from the query results."""

import math
import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Whole numbers in this range read as years, which are not rounded in speech.
YEARS = range(1900, 2101)
# Small whole numbers are usually counts of things on screen ("the top 5").
SMALL = 10

_NUMBER = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|percent|thousand|million|billion)?",
    re.IGNORECASE,
)
_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def numbers_in(values: Iterable[Any]) -> set[float]:
    """Every number found in the values: numeric cells, the parts of dates,
    and digits inside text."""
    found: set[float] = set()
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | float | Decimal):
            found.add(float(value))
        elif isinstance(value, date | datetime):
            found.update((value.year, value.month, value.day))
        elif isinstance(value, str):
            found.update(float(m.replace(",", "")) for m, _ in _NUMBER.findall(value))
    return found


def unsupported_numbers(text: str, allowed: set[float]) -> list[str]:
    """Numbers in the text that match nothing in `allowed`, as written.

    A number matches an allowed value that rounds to it at the precision it
    was written with ("about 12,400" or "12 thousand" for 12,431). Years must
    match exactly. A percentage also matches an allowed fraction ("11%" for
    0.109). A decade ("the mid-2010s") matches any allowed year in it. Whole
    numbers up to SMALL always pass.
    """
    unsupported = []
    for match in _NUMBER.finditer(text):
        digits, unit = match.group(1), (match.group(2) or "").lower()
        number = float(digits.replace(",", "").rstrip("."))
        whole = number == int(number)
        candidates = [number * _SCALES.get(unit, 1)]
        if unit in ("%", "percent"):
            candidates.append(number / 100)
        elif whole and number <= SMALL:
            continue
        exact = whole and not unit and int(number) in YEARS
        decade = exact and number % 10 == 0 and text[match.end(1) :].startswith("s")
        if decade:
            if not any(number <= year <= number + 9 for year in allowed):
                unsupported.append(f"{digits}s")
            continue
        figures = 15 if exact else _significant_figures(digits)
        if not any(_rounds_to(a, c, figures) for c in candidates for a in allowed):
            unsupported.append(match.group(0).strip())
    return unsupported


def _significant_figures(digits: str) -> int:
    """Figures a number was written with: 3 for "12,400" and for "0.109"."""
    plain = digits.replace(",", "")
    if "." not in plain:
        plain = plain.rstrip("0")
    return max(1, len(plain.replace(".", "").lstrip("0")))


def _rounds_to(value: float, number: float, figures: int) -> bool:
    if value == 0 or number == 0:
        return value == number
    magnitude = math.floor(math.log10(abs(value)))
    rounded = round(value, figures - 1 - magnitude)
    return math.isclose(rounded, number, rel_tol=1e-9)
