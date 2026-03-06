"""LLM-based property evaluation using Claude CLI."""

import subprocess

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

PERSONALIZED_ADDENDUM = """\

The buyer has provided the following profile. Tailor your evaluation to \
their specific situation.

Add these additional sections AFTER the standard sections:

## Commute Analysis
Analyze realistic commute options from this property to the buyer's \
workplace. Consider driving (with traffic), public transit (BART, Caltrain, \
bus), and park-and-ride options. Estimate commute times for typical rush \
hour. Mention specific stations, routes, and parking availability. Be \
concrete — name the nearest transit stops and realistic door-to-door times.

## Personal Fit
How well does this property match the buyer's stated preferences and \
concerns? Call out specific matches and mismatches. Be honest about \
dealbreakers.\
"""

SUMMARY_SYSTEM_PROMPT = (
    "You are a real estate analyst. Compare the given properties and "
    "provide a concise ranking with reasoning. Focus on value ($/sqft "
    "relative to area), risk factors, and which properties deserve "
    "serious consideration vs which to skip. Be direct and practical."
)


def _run_claude(system_prompt: str, user_message: str, model: str) -> str:
    """Run a prompt through the Claude CLI in non-interactive mode.

    Args:
        system_prompt: System prompt for the LLM.
        user_message: User message to send.
        model: Claude model name (e.g. "sonnet").

    Returns:
        The text response from Claude.

    Raises:
        RuntimeError: If the claude CLI exits with an error.
    """
    result = subprocess.run(
        [
            "claude",
            "-p",
            "--model", model,
            "--system-prompt", system_prompt,
            user_message,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr}"
        )
    return result.stdout.strip()


def evaluate_property(
    prop: Property,
    page_text: str,
    model: str = "sonnet",
    profile: str | None = None,
) -> str:
    """Send property data to Claude for detailed evaluation.

    If a buyer profile is provided, the evaluation includes personalized
    commute analysis and personal fit sections.

    Args:
        prop: The Property object with basic scraped data.
        page_text: Full text content scraped from the Redfin detail page.
        model: Claude model alias or ID to use.
        profile: Optional free-text buyer profile describing preferences,
            commute needs, concerns, etc.

    Returns:
        The evaluation text from Claude.
    """
    system_prompt = SYSTEM_PROMPT
    if profile:
        system_prompt += PERSONALIZED_ADDENDUM

    user_message = (
        f"Evaluate this property:\n\n"
        f"**{prop.address}, {prop.city}, {prop.state} {prop.zip_code}**\n"
        f"URL: {prop.url}\n\n"
        f"--- Redfin Listing Data ---\n{page_text}\n"
    )

    if profile:
        user_message += f"\n--- Buyer Profile ---\n{profile}\n"

    return _run_claude(system_prompt, user_message, model)


def evaluate_property_summary(
    properties: list[Property],
    page_texts: dict[str, str],
    model: str = "sonnet",
) -> str:
    """Generate a comparative summary across multiple properties.

    Args:
        properties: List of Property objects to compare.
        page_texts: Dict mapping property URL to scraped page text.
        model: Claude model alias or ID to use.

    Returns:
        A comparative analysis from Claude.
    """
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

    return _run_claude(SUMMARY_SYSTEM_PROMPT, user_message, model)
