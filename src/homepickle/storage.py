"""SQLite-based storage for properties, evaluations, and sync state."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from homepickle.models import Property

DB_PATH = Path.home() / ".homepickle" / "homepickle.db"

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

CREATE INDEX IF NOT EXISTS idx_evaluations_url
    ON evaluations(property_url);

CREATE TABLE IF NOT EXISTS favorites_sync (
    property_url    TEXT NOT NULL,
    list_name       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    removed_at      TEXT,
    PRIMARY KEY (property_url, list_name)
);
"""


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO-formatted UTC timestamp.
    """
    return datetime.now(UTC).isoformat()


def get_connection() -> sqlite3.Connection:
    """Open (and initialize if needed) the SQLite database.

    Returns:
        A sqlite3 Connection with WAL mode and foreign keys enabled.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def upsert_property(conn: sqlite3.Connection, prop: Property) -> None:
    """Insert or update a property row keyed by URL.

    Args:
        conn: An open database connection.
        prop: The Property to store.
    """
    if not prop.url:
        return
    now = _now()
    conn.execute(
        """\
        INSERT INTO properties
            (url, address, city, state, zip_code, price, beds, baths, sqft,
             lot_sqft, year_built, days_on_market, hoa, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            address=excluded.address, city=excluded.city, state=excluded.state,
            zip_code=excluded.zip_code, price=excluded.price, beds=excluded.beds,
            baths=excluded.baths, sqft=excluded.sqft, lot_sqft=excluded.lot_sqft,
            year_built=excluded.year_built, days_on_market=excluded.days_on_market,
            hoa=excluded.hoa, updated_at=excluded.updated_at
        """,
        (
            prop.url, prop.address, prop.city, prop.state, prop.zip_code,
            prop.price, prop.beds, prop.baths, prop.sqft, prop.lot_sqft,
            prop.year_built, prop.days_on_market, prop.hoa, now, now,
        ),
    )


def sync_favorites(
    conn: sqlite3.Connection, list_name: str, properties: list[Property]
) -> tuple[list[Property], list[str]]:
    """Update favorites_sync and detect new/removed properties for a list.

    Args:
        conn: An open database connection.
        list_name: The favorites list name.
        properties: Currently scraped properties for this list.

    Returns:
        A tuple of (new_properties, removed_urls).
        new_properties: Properties not previously seen in this list.
        removed_urls: URLs previously active that are no longer present.
    """
    now = _now()
    current_urls = {p.url for p in properties if p.url}

    # Get previously active URLs for this list.
    rows = conn.execute(
        "SELECT property_url FROM favorites_sync "
        "WHERE list_name = ? AND removed_at IS NULL",
        (list_name,),
    ).fetchall()
    prev_urls = {r["property_url"] for r in rows}

    new_urls = current_urls - prev_urls
    removed_urls = prev_urls - current_urls

    # Upsert current properties into favorites_sync.
    for url in current_urls:
        conn.execute(
            """\
            INSERT INTO favorites_sync (property_url, list_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(property_url, list_name) DO UPDATE SET
                last_seen=excluded.last_seen, removed_at=NULL
            """,
            (url, list_name, now, now),
        )

    # Mark removed properties.
    for url in removed_urls:
        conn.execute(
            "UPDATE favorites_sync SET removed_at = ? "
            "WHERE property_url = ? AND list_name = ?",
            (now, url, list_name),
        )

    new_properties = [p for p in properties if p.url in new_urls]
    return new_properties, sorted(removed_urls)


def save_evaluation(
    conn: sqlite3.Connection,
    property_url: str,
    model: str,
    evaluation_text: str,
    page_text_hash: str,
    price_at_eval: int | None,
) -> None:
    """Store an evaluation result.

    Args:
        conn: An open database connection.
        property_url: The property URL this evaluation is for.
        model: The model used for evaluation.
        evaluation_text: The full evaluation text.
        page_text_hash: Hash of the page text used for evaluation.
        price_at_eval: The listing price when the evaluation was made.
    """
    conn.execute(
        """\
        INSERT INTO evaluations
            (property_url, model, evaluation_text, page_text_hash,
             price_at_eval, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (property_url, model, evaluation_text, page_text_hash,
         price_at_eval, _now()),
    )


def get_latest_evaluation(
    conn: sqlite3.Connection, property_url: str
) -> sqlite3.Row | None:
    """Fetch the most recent evaluation for a property.

    Args:
        conn: An open database connection.
        property_url: The property URL to look up.

    Returns:
        A Row with evaluation data, or None if no evaluation exists.
    """
    return conn.execute(
        "SELECT * FROM evaluations WHERE property_url = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (property_url,),
    ).fetchone()


def needs_evaluation(
    conn: sqlite3.Connection, property_url: str, current_price: int | None
) -> bool:
    """Check whether a property needs (re-)evaluation.

    A property needs evaluation if:
    - It has never been evaluated, OR
    - Its listing price has changed since the last evaluation.

    Args:
        conn: An open database connection.
        property_url: The property URL to check.
        current_price: The current listing price.

    Returns:
        True if the property should be evaluated.
    """
    row = get_latest_evaluation(conn, property_url)
    if row is None:
        return True
    return row["price_at_eval"] != current_price


def get_all_evaluations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Fetch the latest evaluation for every property that has one.

    Args:
        conn: An open database connection.

    Returns:
        A list of Rows, one per property, most recently evaluated first.
    """
    return conn.execute(
        """\
        SELECT e.*, p.address, p.city, p.state, p.price, p.beds, p.baths, p.sqft
        FROM evaluations e
        JOIN properties p ON e.property_url = p.url
        WHERE e.id = (
            SELECT MAX(e2.id) FROM evaluations e2
            WHERE e2.property_url = e.property_url
        )
        ORDER BY e.created_at DESC
        """,
    ).fetchall()


def get_all_properties(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Fetch all tracked properties.

    Args:
        conn: An open database connection.

    Returns:
        A list of property Rows.
    """
    return conn.execute(
        "SELECT * FROM properties ORDER BY updated_at DESC"
    ).fetchall()


def get_property(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """Fetch a single property by URL.

    Args:
        conn: An open database connection.
        url: The property URL to look up.

    Returns:
        A property Row, or None if not found.
    """
    return conn.execute(
        "SELECT * FROM properties WHERE url = ?", (url,)
    ).fetchone()


def get_favorite_list_names(conn: sqlite3.Connection) -> list[str]:
    """Fetch all distinct favorite list names.

    Args:
        conn: An open database connection.

    Returns:
        A sorted list of list names.
    """
    rows = conn.execute(
        "SELECT DISTINCT list_name FROM favorites_sync ORDER BY list_name"
    ).fetchall()
    return [r["list_name"] for r in rows]


def get_properties_for_list(
    conn: sqlite3.Connection, list_name: str
) -> list[sqlite3.Row]:
    """Fetch all active properties in a favorites list.

    Args:
        conn: An open database connection.
        list_name: The favorites list name.

    Returns:
        A list of property Rows.
    """
    return conn.execute(
        """\
        SELECT p.* FROM properties p
        JOIN favorites_sync fs ON p.url = fs.property_url
        WHERE fs.list_name = ? AND fs.removed_at IS NULL
        ORDER BY p.updated_at DESC
        """,
        (list_name,),
    ).fetchall()


def get_distinct_cities(conn: sqlite3.Connection) -> list[str]:
    """Fetch all distinct cities from tracked properties.

    Args:
        conn: An open database connection.

    Returns:
        A sorted list of city names.
    """
    rows = conn.execute(
        "SELECT DISTINCT city FROM properties WHERE city != '' ORDER BY city"
    ).fetchall()
    return [r["city"] for r in rows]


def row_to_property(row: sqlite3.Row) -> Property:
    """Convert a database Row to a Property object.

    Args:
        row: A sqlite3.Row from the properties table.

    Returns:
        A Property instance.
    """
    return Property(
        address=row["address"],
        city=row["city"],
        state=row["state"],
        zip_code=row["zip_code"],
        price=row["price"],
        beds=row["beds"],
        baths=row["baths"],
        sqft=row["sqft"],
        lot_sqft=row["lot_sqft"],
        year_built=row["year_built"],
        days_on_market=row["days_on_market"],
        hoa=row["hoa"],
        url=row["url"],
    )
