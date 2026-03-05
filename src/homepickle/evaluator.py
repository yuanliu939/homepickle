"""LLM-based property evaluation using Claude."""

import os

import anthropic

from homepickle.models import Property

SYSTEM_PROMPT = """\
You are a real estate analyst helping a homebuyer evaluate properties.
Given detailed Redfin listing data for a property, provide a thorough evaluation.

Structure your response with these sections:
## Snapshot
Key listing facts (price, $/sqft, size, lot, beds/baths, year built, utilities).

## Big Positives
What makes this property attractive (location, views, upgrades, schools, etc.).

## Key Concerns
Risks and hidden costs to investigate (fire risk, well/septic, foundation, \
insurance, zoning constraints, deferred maintenance, etc.). Be specific and \
actionable.

## Price Reality Check
How does the $/sqft compare to the area? Does the price feel justified given \
the property's characteristics? Note any price history (reductions, relists).

## Ownership Cost Estimates
Property taxes (especially post-purchase reassessment in CA), insurance \
concerns, HOA, and any unusual ongoing costs.

## Due Diligence Checklist
Numbered list of the most important things to verify before making an offer.

Be direct, specific, and practical. Flag real risks honestly — don't sugarcoat. \
Use the actual data from the listing, not generic advice.\
"""


def evaluate_property(
    prop: Property, page_text: str, model: str = "claude-sonnet-4-20250514"
) -> str:
    """Send property data to Claude for detailed evaluation.

    Args:
        prop: The Property object with basic scraped data.
        page_text: Full text content scraped from the Redfin detail page.
        model: Claude model ID to use.

    Returns:
        The evaluation text from Claude.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required. "
            "Set it with: export ANTHROPIC_API_KEY=sk-..."
        )

    client = anthropic.Anthropic(api_key=api_key)

    user_message = (
        f"Evaluate this property:\n\n"
        f"**{prop.address}, {prop.city}, {prop.state} {prop.zip_code}**\n"
        f"URL: {prop.url}\n\n"
        f"--- Redfin Listing Data ---\n{page_text}\n"
    )

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text


def evaluate_property_summary(
    properties: list[Property],
    page_texts: dict[str, str],
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Generate a comparative summary across multiple properties.

    Args:
        properties: List of Property objects to compare.
        page_texts: Dict mapping property URL to scraped page text.
        model: Claude model ID to use.

    Returns:
        A comparative analysis from Claude.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required. "
            "Set it with: export ANTHROPIC_API_KEY=sk-..."
        )

    client = anthropic.Anthropic(api_key=api_key)

    listing_summaries = []
    for p in properties:
        text = page_texts.get(p.url or "", "")
        # Truncate each listing to keep within context limits.
        snippet = text[:3000] if text else "(no detail data)"
        listing_summaries.append(
            f"### {p.address}, {p.city} — "
            f"${p.price:,} | {p.beds}bd/{p.baths}ba | "
            f"{p.sqft} sqft | ${p.price_per_sqft:,.0f}/sqft\n"
            f"{snippet}\n"
        )

    user_message = (
        f"Compare and rank these {len(properties)} properties. "
        f"Identify the best values, biggest risks, and your top picks.\n\n"
        + "\n---\n".join(listing_summaries)
    )

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=(
            "You are a real estate analyst. Compare the given properties and "
            "provide a concise ranking with reasoning. Focus on value ($/sqft "
            "relative to area), risk factors, and which properties deserve "
            "serious consideration vs which to skip. Be direct and practical."
        ),
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text
