"""Analyze property data and surface insights."""

from homepickle.models import Property


def median(values: list[float]) -> float | None:
    """Calculate the median of a list of numbers.

    Args:
        values: A list of numeric values.

    Returns:
        The median value, or None if the list is empty.
    """
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def summarize_prices(properties: list[Property]) -> dict[str, float | None]:
    """Summarize price statistics across a list of properties.

    Args:
        properties: A list of Property objects.

    Returns:
        A dict with keys 'min', 'max', 'median', and 'median_price_per_sqft'.
    """
    prices = [p.price for p in properties if p.price is not None]
    price_per_sqft = [
        p.price_per_sqft for p in properties if p.price_per_sqft is not None
    ]

    return {
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "median": median(prices),
        "median_price_per_sqft": median(price_per_sqft),
    }
