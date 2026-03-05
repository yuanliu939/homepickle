"""Tests for property analysis functions."""

from homepickle.analyzer import median, summarize_prices
from homepickle.models import Property


def _make_property(
    price: int | None = None, sqft: int | None = None
) -> Property:
    """Create a Property with minimal required fields for testing.

    Args:
        price: Optional listing price.
        sqft: Optional square footage.

    Returns:
        A Property instance.
    """
    return Property(
        address="123 Main St", city="Seattle", state="WA", zip_code="98101",
        price=price, sqft=sqft,
    )


def test_median_odd() -> None:
    """Median of odd-length list returns the middle value."""
    assert median([3, 1, 2]) == 2


def test_median_even() -> None:
    """Median of even-length list returns the average of two middle values."""
    assert median([1, 2, 3, 4]) == 2.5


def test_median_empty() -> None:
    """Median of empty list returns None."""
    assert median([]) is None


def test_summarize_prices() -> None:
    """Summarize returns correct min, max, median, and price per sqft."""
    props = [
        _make_property(price=300_000, sqft=1500),
        _make_property(price=500_000, sqft=2000),
        _make_property(price=400_000, sqft=1800),
    ]
    result = summarize_prices(props)
    assert result["min"] == 300_000
    assert result["max"] == 500_000
    assert result["median"] == 400_000


def test_summarize_prices_empty() -> None:
    """Summarize returns all None for empty list."""
    result = summarize_prices([])
    assert result["min"] is None
    assert result["max"] is None
    assert result["median"] is None
    assert result["median_price_per_sqft"] is None
