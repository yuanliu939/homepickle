"""Tests for property data models."""

from homepickle.models import FavoriteList, Property


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


def test_favorite_list_defaults_empty() -> None:
    """A new FavoriteList has an empty properties list."""
    fav = FavoriteList(name="My Homes")
    assert fav.properties == []
    assert fav.url is None
