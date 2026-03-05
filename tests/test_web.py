"""Tests for the Flask web application."""

import sqlite3
from unittest.mock import patch

from homepickle.web import create_app


def _seed_db(conn: sqlite3.Connection) -> None:
    """Insert test data into an in-memory database.

    Args:
        conn: An open database connection with schema applied.
    """
    conn.execute(
        "INSERT INTO properties VALUES "
        "('https://redfin.com/1', '123 Main St', 'Seattle', 'WA', '98101', "
        "500000, 3, 2.0, 1500, NULL, NULL, NULL, NULL, "
        "'https://ssl.cdn-redfin.com/photo/1.jpg', "
        "'2025-01-01', '2025-01-01')"
    )
    conn.execute(
        "INSERT INTO properties VALUES "
        "('https://redfin.com/2', '456 Oak Ave', 'Portland', 'OR', '97201', "
        "400000, 2, 1.0, 1200, NULL, NULL, NULL, NULL, "
        "NULL, '2025-01-01', '2025-01-01')"
    )
    conn.execute(
        "INSERT INTO evaluations VALUES "
        "(1, 'https://redfin.com/1', 'sonnet', '## Snapshot\nGreat house.', "
        "'abc123', 500000, '2025-01-02')"
    )
    conn.execute(
        "INSERT INTO favorites_sync VALUES "
        "('https://redfin.com/1', 'My Homes', '2025-01-01', '2025-01-01', NULL)"
    )
    conn.execute(
        "INSERT INTO favorites_sync VALUES "
        "('https://redfin.com/2', 'My Homes', '2025-01-01', '2025-01-01', NULL)"
    )
    conn.commit()


def _make_test_conn() -> sqlite3.Connection:
    """Create a seeded in-memory database.

    Returns:
        A sqlite3 Connection with test data.
    """
    from homepickle.storage import _SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _seed_db(conn)
    return conn


def test_index_page() -> None:
    """Index page renders with property table."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "123 Main St" in html
        assert "456 Oak Ave" in html
        assert "Seattle" in html


def test_index_filter_by_city() -> None:
    """Index page filters by city query parameter."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/?city=Seattle")
        html = resp.data.decode()
        assert "123 Main St" in html
        assert "456 Oak Ave" not in html


def test_index_filter_by_list() -> None:
    """Index page filters by list query parameter."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/?list=My+Homes")
        html = resp.data.decode()
        assert "123 Main St" in html


def test_property_detail_with_evaluation() -> None:
    """Property detail page shows evaluation content."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/property?url=https://redfin.com/1")
        html = resp.data.decode()
        assert "123 Main St" in html
        assert "Great house." in html
        assert "Snapshot" in html


def test_property_detail_without_evaluation() -> None:
    """Property detail page shows pending state when no evaluation."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/property?url=https://redfin.com/2")
        html = resp.data.decode()
        assert "456 Oak Ave" in html
        assert "No evaluation yet" in html


def test_index_shows_property_image() -> None:
    """Index page renders property thumbnail images."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/")
        html = resp.data.decode()
        assert "ssl.cdn-redfin.com/photo/1.jpg" in html
        assert "property-thumb" in html


def test_property_detail_shows_hero_image() -> None:
    """Property detail page renders the hero image."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/property?url=https://redfin.com/1")
        html = resp.data.decode()
        assert "prop-hero" in html
        assert "ssl.cdn-redfin.com/photo/1.jpg" in html


def test_property_detail_not_found() -> None:
    """Property detail page handles unknown URL."""
    conn = _make_test_conn()
    with patch("homepickle.web.get_connection", return_value=conn):
        app = create_app()
        client = app.test_client()
        resp = client.get("/property?url=https://redfin.com/nope")
        html = resp.data.decode()
        assert "Property not found" in html
