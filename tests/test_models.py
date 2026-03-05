"""Tests for property data models."""

from homepickle.models import Property, SavedSearch


def test_price_per_sqft() -> None:
    """Price per sqft is calculated when both price and sqft are set."""
    prop = Property(
        address="123 Main St", city="Seattle", state="WA", zip_code="98101",
        price=500_000, sqft=2000,
    )
    assert prop.price_per_sqft == 250.0


def test_price_per_sqft_missing_price() -> None:
    """Price per sqft is None when price is missing."""
    prop = Property(
        address="123 Main St", city="Seattle", state="WA", zip_code="98101",
        sqft=2000,
    )
    assert prop.price_per_sqft is None


def test_price_per_sqft_missing_sqft() -> None:
    """Price per sqft is None when sqft is missing."""
    prop = Property(
        address="123 Main St", city="Seattle", state="WA", zip_code="98101",
        price=500_000,
    )
    assert prop.price_per_sqft is None


def test_saved_search_defaults_empty() -> None:
    """A new SavedSearch has an empty properties list."""
    search = SavedSearch(name="Test Search")
    assert search.properties == []
