"""Analyze property data and surface insights."""

from collections import defaultdict
from dataclasses import dataclass

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


@dataclass
class CityStats:
    """Aggregated statistics for properties in a single city."""

    city: str
    count: int
    median_price: float | None
    median_price_per_sqft: float | None


def group_by_city(properties: list[Property]) -> list[CityStats]:
    """Group properties by city and compute per-city statistics.

    Args:
        properties: A list of Property objects.

    Returns:
        A list of CityStats sorted by count descending.
    """
    by_city: dict[str, list[Property]] = defaultdict(list)
    for p in properties:
        if p.city:
            by_city[p.city].append(p)

    results = []
    for city, props in by_city.items():
        prices = [p.price for p in props if p.price is not None]
        ppsf = [p.price_per_sqft for p in props if p.price_per_sqft is not None]
        results.append(
            CityStats(
                city=city,
                count=len(props),
                median_price=median(prices),
                median_price_per_sqft=median(ppsf),
            )
        )

    return sorted(results, key=lambda s: s.count, reverse=True)


@dataclass
class ValueOutlier:
    """A property that deviates significantly from its city's median $/sqft."""

    property: Property
    city_median_ppsf: float
    ppsf: float
    pct_diff: float


def find_value_outliers(
    properties: list[Property], threshold: float = 0.2
) -> tuple[list[ValueOutlier], list[ValueOutlier]]:
    """Find properties priced significantly above or below their city median.

    Compares each property's $/sqft to the median $/sqft of properties in the
    same city. Properties that deviate by more than `threshold` (default 20%)
    are flagged.

    Args:
        properties: A list of Property objects.
        threshold: Fraction deviation to flag (e.g. 0.2 = 20%).

    Returns:
        A tuple of (underpriced, overpriced) lists of ValueOutlier.
    """
    city_stats = {s.city: s for s in group_by_city(properties)}

    underpriced: list[ValueOutlier] = []
    overpriced: list[ValueOutlier] = []

    for p in properties:
        if p.price_per_sqft is None or not p.city:
            continue
        stats = city_stats.get(p.city)
        if not stats or stats.median_price_per_sqft is None or stats.count < 2:
            continue

        city_med = stats.median_price_per_sqft
        pct_diff = (p.price_per_sqft - city_med) / city_med

        if pct_diff < -threshold:
            underpriced.append(
                ValueOutlier(
                    property=p,
                    city_median_ppsf=city_med,
                    ppsf=p.price_per_sqft,
                    pct_diff=pct_diff,
                )
            )
        elif pct_diff > threshold:
            overpriced.append(
                ValueOutlier(
                    property=p,
                    city_median_ppsf=city_med,
                    ppsf=p.price_per_sqft,
                    pct_diff=pct_diff,
                )
            )

    underpriced.sort(key=lambda o: o.pct_diff)
    overpriced.sort(key=lambda o: o.pct_diff, reverse=True)
    return underpriced, overpriced


def format_report(properties: list[Property]) -> str:
    """Generate a formatted text report of property analysis.

    Includes: overall price summary, city breakdown, value outliers, and
    a comparison table sorted by $/sqft.

    Args:
        properties: A list of Property objects.

    Returns:
        A formatted multi-line string.
    """
    lines: list[str] = []

    # --- Overall summary ---
    summary = summarize_prices(properties)
    lines.append(f"=== Price Summary ({len(properties)} properties) ===")
    lines.append(f"  Min:              {_fmt_price(summary['min'])}")
    lines.append(f"  Max:              {_fmt_price(summary['max'])}")
    lines.append(f"  Median:           {_fmt_price(summary['median'])}")
    lines.append(
        f"  Median $/sqft:    {_fmt_ppsf(summary['median_price_per_sqft'])}"
    )
    lines.append("")

    # --- City breakdown ---
    city_stats = group_by_city(properties)
    lines.append("=== By City ===")
    lines.append(
        f"  {'City':<20} {'Count':>5}  {'Med Price':>12}  {'Med $/sqft':>10}"
    )
    lines.append(f"  {'-'*20} {'-'*5}  {'-'*12}  {'-'*10}")
    for cs in city_stats:
        lines.append(
            f"  {cs.city:<20} {cs.count:>5}  "
            f"{_fmt_price(cs.median_price):>12}  "
            f"{_fmt_ppsf(cs.median_price_per_sqft):>10}"
        )
    lines.append("")

    # --- Value outliers ---
    underpriced, overpriced = find_value_outliers(properties)
    if underpriced:
        lines.append("=== Potential Values (below city median $/sqft) ===")
        for o in underpriced:
            p = o.property
            lines.append(
                f"  {p.address}, {p.city}  "
                f"{_fmt_ppsf(o.ppsf)} vs city {_fmt_ppsf(o.city_median_ppsf)}  "
                f"({o.pct_diff:+.0%})"
            )
        lines.append("")

    if overpriced:
        lines.append("=== Premium Priced (above city median $/sqft) ===")
        for o in overpriced:
            p = o.property
            lines.append(
                f"  {p.address}, {p.city}  "
                f"{_fmt_ppsf(o.ppsf)} vs city {_fmt_ppsf(o.city_median_ppsf)}  "
                f"({o.pct_diff:+.0%})"
            )
        lines.append("")

    # --- Comparison table ---
    with_ppsf = [p for p in properties if p.price_per_sqft is not None]
    with_ppsf.sort(key=lambda p: p.price_per_sqft)  # type: ignore[arg-type]
    lines.append("=== All Properties (sorted by $/sqft) ===")
    lines.append(
        f"  {'Address':<30} {'City':<18} {'Price':>12} "
        f"{'Beds':>4} {'Bath':>5} {'SqFt':>7} {'$/sqft':>8}"
    )
    lines.append(
        f"  {'-'*30} {'-'*18} {'-'*12} "
        f"{'-'*4} {'-'*5} {'-'*7} {'-'*8}"
    )
    for p in with_ppsf:
        lines.append(
            f"  {p.address[:30]:<30} {p.city[:18]:<18} "
            f"{_fmt_price(p.price):>12} "
            f"{_fmt_int(p.beds):>4} "
            f"{_fmt_float(p.baths):>5} "
            f"{_fmt_int(p.sqft):>7} "
            f"{_fmt_ppsf(p.price_per_sqft):>8}"
        )

    return "\n".join(lines)


def _fmt_price(value: float | None) -> str:
    """Format a price as a dollar string.

    Args:
        value: Price in dollars, or None.

    Returns:
        Formatted string like '$1,234,567' or '-'.
    """
    if value is None:
        return "-"
    return f"${value:,.0f}"


def _fmt_ppsf(value: float | None) -> str:
    """Format price per sqft as a dollar string.

    Args:
        value: Price per sqft, or None.

    Returns:
        Formatted string like '$450' or '-'.
    """
    if value is None:
        return "-"
    return f"${value:,.0f}"


def _fmt_int(value: int | None) -> str:
    """Format an optional integer.

    Args:
        value: Integer or None.

    Returns:
        The integer as a string, or '-'.
    """
    if value is None:
        return "-"
    return str(value)


def _fmt_float(value: float | None) -> str:
    """Format an optional float with one decimal.

    Args:
        value: Float or None.

    Returns:
        The float as a string, or '-'.
    """
    if value is None:
        return "-"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"
