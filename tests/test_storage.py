"""Tests for the SQLite storage layer."""

import sqlite3

from homepickle.models import Property
from homepickle.storage import (
    _SCHEMA,
    get_latest_evaluation,
    get_latest_personalized_evaluation,
    get_profile,
    needs_evaluation,
    needs_personalized_evaluation,
    save_evaluation,
    save_personalized_evaluation,
    save_profile,
    sync_favorites,
    upsert_property,
)


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory database with the schema applied.

    Returns:
        A sqlite3 Connection to an in-memory database.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _make_property(
    url: str = "https://redfin.com/home/1",
    price: int | None = 500_000,
    city: str = "Seattle",
) -> Property:
    """Create a Property with minimal fields for testing.

    Args:
        url: Property URL.
        price: Listing price.
        city: City name.

    Returns:
        A Property instance.
    """
    return Property(
        address="123 Main St", city=city, state="WA", zip_code="98101",
        price=price, url=url,
    )


def test_upsert_property_insert() -> None:
    """Inserting a new property creates a row."""
    conn = _make_conn()
    prop = _make_property()
    upsert_property(conn, prop)
    conn.commit()

    row = conn.execute("SELECT * FROM properties WHERE url = ?", (prop.url,)).fetchone()
    assert row is not None
    assert row["address"] == "123 Main St"
    assert row["price"] == 500_000


def test_upsert_property_update() -> None:
    """Upserting an existing property updates the row."""
    conn = _make_conn()
    prop = _make_property(price=500_000)
    upsert_property(conn, prop)
    conn.commit()

    prop.price = 475_000
    upsert_property(conn, prop)
    conn.commit()

    row = conn.execute("SELECT * FROM properties WHERE url = ?", (prop.url,)).fetchone()
    assert row["price"] == 475_000


def test_upsert_property_no_url() -> None:
    """Properties without URLs are silently skipped."""
    conn = _make_conn()
    prop = _make_property()
    prop.url = None
    upsert_property(conn, prop)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    assert count == 0


def test_sync_favorites_new() -> None:
    """First sync marks all properties as new."""
    conn = _make_conn()
    props = [_make_property(url=f"https://redfin.com/home/{i}") for i in range(3)]
    for p in props:
        upsert_property(conn, p)
    conn.commit()

    new, removed = sync_favorites(conn, "My List", props)
    conn.commit()

    assert len(new) == 3
    assert len(removed) == 0


def test_sync_favorites_no_change() -> None:
    """Second sync with same properties reports nothing new."""
    conn = _make_conn()
    props = [_make_property(url=f"https://redfin.com/home/{i}") for i in range(3)]
    for p in props:
        upsert_property(conn, p)
    conn.commit()

    sync_favorites(conn, "My List", props)
    conn.commit()

    new, removed = sync_favorites(conn, "My List", props)
    assert len(new) == 0
    assert len(removed) == 0


def test_sync_favorites_removal() -> None:
    """Removing a property from favorites is detected."""
    conn = _make_conn()
    props = [_make_property(url=f"https://redfin.com/home/{i}") for i in range(3)]
    for p in props:
        upsert_property(conn, p)
    conn.commit()

    sync_favorites(conn, "My List", props)
    conn.commit()

    # Remove the last property.
    new, removed = sync_favorites(conn, "My List", props[:2])
    assert len(new) == 0
    assert len(removed) == 1
    assert removed[0] == "https://redfin.com/home/2"


def test_sync_favorites_addition() -> None:
    """Adding a new property to favorites is detected."""
    conn = _make_conn()
    props = [_make_property(url=f"https://redfin.com/home/{i}") for i in range(2)]
    for p in props:
        upsert_property(conn, p)
    conn.commit()

    sync_favorites(conn, "My List", props)
    conn.commit()

    new_prop = _make_property(url="https://redfin.com/home/99")
    upsert_property(conn, new_prop)
    conn.commit()

    new, removed = sync_favorites(conn, "My List", props + [new_prop])
    assert len(new) == 1
    assert new[0].url == "https://redfin.com/home/99"
    assert len(removed) == 0


def test_save_and_get_evaluation() -> None:
    """Saving an evaluation and retrieving it works."""
    conn = _make_conn()
    prop = _make_property()
    upsert_property(conn, prop)
    conn.commit()

    save_evaluation(conn, prop.url, "sonnet", "Great property!", "abc123", 500_000)
    conn.commit()

    row = get_latest_evaluation(conn, prop.url)
    assert row is not None
    assert row["evaluation_text"] == "Great property!"
    assert row["price_at_eval"] == 500_000


def test_get_latest_evaluation_none() -> None:
    """Returns None when no evaluation exists."""
    conn = _make_conn()
    row = get_latest_evaluation(conn, "https://redfin.com/home/nope")
    assert row is None


def test_needs_evaluation_no_prior() -> None:
    """A property with no evaluation needs one."""
    conn = _make_conn()
    assert needs_evaluation(conn, "https://redfin.com/home/1", 500_000) is True


def test_needs_evaluation_same_price() -> None:
    """A property with an evaluation at the same price does not need one."""
    conn = _make_conn()
    prop = _make_property()
    upsert_property(conn, prop)
    save_evaluation(conn, prop.url, "sonnet", "Eval text", "hash", 500_000)
    conn.commit()

    assert needs_evaluation(conn, prop.url, 500_000) is False


def test_needs_evaluation_price_changed() -> None:
    """A property with a price change needs re-evaluation."""
    conn = _make_conn()
    prop = _make_property()
    upsert_property(conn, prop)
    save_evaluation(conn, prop.url, "sonnet", "Eval text", "hash", 500_000)
    conn.commit()

    assert needs_evaluation(conn, prop.url, 475_000) is True


def test_profile_empty() -> None:
    """Returns None when no profile exists."""
    conn = _make_conn()
    assert get_profile(conn) is None


def test_save_and_get_profile() -> None:
    """Saving a profile and retrieving it works."""
    conn = _make_conn()
    save_profile(conn, "3+ beds, near BART, work at 500 Howard St SF")
    row = get_profile(conn)
    assert row is not None
    assert "500 Howard St" in row["preferences"]


def test_personalized_evaluation_lifecycle() -> None:
    """Save, retrieve, and check staleness of personalized evaluations."""
    conn = _make_conn()
    prop = _make_property()
    upsert_property(conn, prop)
    save_evaluation(conn, prop.url, "opus", "Base eval text", "hash1", 500_000)
    conn.commit()

    base = get_latest_evaluation(conn, prop.url)
    profile = "I work at 500 Howard St SF, need 3+ beds"

    # No personalized eval exists yet.
    assert needs_personalized_evaluation(conn, prop.url, base["id"], profile)

    save_personalized_evaluation(
        conn, prop.url, base["id"], "opus", "Personalized text", profile
    )
    conn.commit()

    # Now it exists and is up to date.
    assert not needs_personalized_evaluation(conn, prop.url, base["id"], profile)

    row = get_latest_personalized_evaluation(conn, prop.url)
    assert row is not None
    assert row["evaluation_text"] == "Personalized text"
    assert row["base_eval_id"] == base["id"]

    # Profile change triggers re-personalization.
    new_profile = "Different preferences entirely"
    assert needs_personalized_evaluation(conn, prop.url, base["id"], new_profile)

    # New base eval triggers re-personalization.
    save_evaluation(conn, prop.url, "opus", "New base", "hash2", 500_000)
    conn.commit()
    new_base = get_latest_evaluation(conn, prop.url)
    assert needs_personalized_evaluation(conn, prop.url, new_base["id"], profile)


def test_save_profile_upsert() -> None:
    """Saving a profile twice updates rather than duplicates."""
    conn = _make_conn()
    save_profile(conn, "pref1")
    save_profile(conn, "pref2")
    row = get_profile(conn)
    assert row["preferences"] == "pref2"
    count = conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0]
    assert count == 1
