"""Tests for property analysis functions."""

from homepickle.analyzer import (
    find_value_outliers,
    group_by_city,
    median,
    summarize_prices,
)
from homepickle.models import Property


def _make_property(
    price: int | None = None,
    sqft: int | None = None,
    city: str = "Seattle",
) -> Property:
    """Create a Property with minimal required fields for testing.

    Args:
        price: Optional listing price.
        sqft: Optional square footage.
        city: City name.

    Returns:
        A Property instance.
    """
    return Property(
        address="123 Main St", city=city, state="WA", zip_code="98101",
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


def test_group_by_city() -> None:
    """Properties are grouped by city with correct counts and medians."""
    props = [
        _make_property(price=500_000, sqft=2000, city="Seattle"),
        _make_property(price=600_000, sqft=2000, city="Seattle"),
        _make_property(price=300_000, sqft=1500, city="Tacoma"),
    ]
    stats = group_by_city(props)
    assert len(stats) == 2
    # Seattle has more properties, so it comes first.
    assert stats[0].city == "Seattle"
    assert stats[0].count == 2
    assert stats[0].median_price == 550_000


def test_find_value_outliers() -> None:
    """Outlier detection flags properties deviating from city median."""
    props = [
        _make_property(price=500_000, sqft=2000, city="Seattle"),  # $250/sqft
        _make_property(price=500_000, sqft=2000, city="Seattle"),  # $250/sqft
        _make_property(price=300_000, sqft=2000, city="Seattle"),  # $150/sqft
    ]
    underpriced, overpriced = find_value_outliers(props)
    assert len(underpriced) == 1
    assert underpriced[0].property.price == 300_000
    assert len(overpriced) == 0
