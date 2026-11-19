from datetime import date
from decimal import Decimal

from talkviz.speech import numbers_in, unsupported_numbers


def test_numbers_in_reads_cells_dates_and_text():
    found = numbers_in(
        [12431, 0.109, Decimal("7.5"), date(2016, 3, 9), "F-150", None, True]
    )
    assert found == {12431.0, 0.109, 7.5, 2016.0, 3.0, 9.0, 150.0}


def test_exact_and_rounded_numbers_are_supported():
    allowed = {12431.0, 2016.0}
    assert unsupported_numbers("It peaked at 12,431 in 2016.", allowed) == []
    assert unsupported_numbers("It peaked at about 12,400 in 2016.", allowed) == []
    assert unsupported_numbers("About 12.4 thousand in 2016.", allowed) == []
    assert unsupported_numbers("Roughly 12,000 that year.", allowed) == []


def test_numbers_not_in_the_results_are_reported():
    allowed = {12431.0, 2016.0}
    assert unsupported_numbers("It peaked at 15,000 in 2016.", allowed) == ["15,000"]
    assert unsupported_numbers("It rose 37% by 2019.", allowed) == ["37%", "2019"]
    # A year is never a rounding of another year, or of a nearby count.
    assert unsupported_numbers("It peaked in 2020.", allowed) == ["2020"]
    assert unsupported_numbers("It peaked at 12,900.", allowed) == ["12,900"]


def test_percentages_match_fractions_or_percent_values():
    assert unsupported_numbers("About 11% reported a crash.", {0.109}) == []
    assert unsupported_numbers("About 11 percent did.", {10.9}) == []
    assert unsupported_numbers("About 30% did.", {0.109}) == ["30%"]


def test_small_whole_numbers_always_pass():
    assert unsupported_numbers("The top 5 makes, and 3 stand out.", set()) == []
    assert unsupported_numbers("There were 11 of them.", set()) == ["11"]
    assert unsupported_numbers("It was 2.5 times higher.", set()) == ["2.5"]


def test_a_decade_is_supported_by_any_year_in_it():
    allowed = {2013.0, 2015.0, 0.438}
    assert (
        unsupported_numbers("Highest in the mid-2010s, at 44 percent.", allowed) == []
    )
    assert unsupported_numbers("It fell through the 2020s.", allowed) == ["2020s"]
    # A bare year is still exact.
    assert unsupported_numbers("It peaked in 2010.", allowed) == ["2010"]
