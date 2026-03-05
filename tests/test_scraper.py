"""Tests for scraper parsing helpers."""

from homepickle.scraper import _parse_address, _parse_price, _parse_stats


def test_parse_address_full() -> None:
    """Parse a complete address string into components."""
    addr, city, state, zip_code = _parse_address(
        "123 Main St, Seattle, WA 98101"
    )
    assert addr == "123 Main St"
    assert city == "Seattle"
    assert state == "WA"
    assert zip_code == "98101"


def test_parse_address_multiline() -> None:
    """Parse a multi-line address (newlines become commas)."""
    addr, city, state, zip_code = _parse_address(
        "456 Oak Ave\nPortland, OR 97201"
    )
    assert addr == "456 Oak Ave"
    assert city == "Portland"
    assert state == "OR"
    assert zip_code == "97201"


def test_parse_address_fallback() -> None:
    """Unparseable address goes entirely into the address field."""
    addr, city, state, zip_code = _parse_address("Some weird format")
    assert addr == "Some weird format"
    assert city == ""


def test_parse_price() -> None:
    """Parse a dollar-formatted price string."""
    assert _parse_price("$500,000") == 500_000


def test_parse_price_empty() -> None:
    """Empty or non-numeric string returns None."""
    assert _parse_price("") is None
    assert _parse_price("N/A") is None


def test_parse_stats_full() -> None:
    """Parse a stats string with beds, baths, and sqft."""
    beds, baths, sqft = _parse_stats("3 Beds 2 Baths 1,500 Sq Ft")
    assert beds == 3
    assert baths == 2.0
    assert sqft == 1500


def test_parse_stats_partial() -> None:
    """Parse a stats string with only some fields present."""
    beds, baths, sqft = _parse_stats("2 Beds")
    assert beds == 2
    assert baths is None
    assert sqft is None
