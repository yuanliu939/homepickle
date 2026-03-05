"""Data models for Redfin property listings."""

from dataclasses import dataclass, field


@dataclass
class Property:
    """A single property listing from Redfin."""

    address: str
    city: str
    state: str
    zip_code: str
    price: int | None = None
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = None
    lot_sqft: int | None = None
    year_built: int | None = None
    days_on_market: int | None = None
    hoa: int | None = None
    url: str | None = None

    @property
    def price_per_sqft(self) -> float | None:
        """Calculate price per square foot.

        Returns:
            Price per square foot, or None if price or sqft is missing.
        """
        if self.price is not None and self.sqft:
            return self.price / self.sqft
        return None


@dataclass
class FavoriteList:
    """A favorites list from Redfin containing saved homes."""

    name: str
    url: str | None = None
    properties: list[Property] = field(default_factory=list)
