"""Tests for the SQLite storage layer."""

import sqlite3

from homepickle.models import Property
from homepickle.storage import (
    get_latest_evaluation,
    needs_evaluation,
    save_evaluation,
    sync_favorites,
    upsert_property,
)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS properties (
    url             TEXT PRIMARY KEY,
    address         TEXT NOT NULL,
    city            TEXT NOT NULL,
    state           TEXT NOT NULL,
    zip_code        TEXT NOT NULL,
    price           INTEGER,
    beds            INTEGER,
    baths           REAL,
    sqft            INTEGER,
    lot_sqft        INTEGER,
    year_built      INTEGER,
    days_on_market  INTEGER,
    hoa             INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    property_url    TEXT NOT NULL REFERENCES properties(url),
    model           TEXT NOT NULL,
    evaluation_text TEXT NOT NULL,
    page_text_hash  TEXT NOT NULL,
    price_at_eval   INTEGER,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites_sync (
    property_url    TEXT NOT NULL,
    list_name       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    removed_at      TEXT,
    PRIMARY KEY (property_url, list_name)
);
"""


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
