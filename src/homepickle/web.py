"""Flask web application for browsing property evaluations."""

import re

from flask import Flask, jsonify, render_template, request
from markupsafe import Markup

from homepickle.storage import (
    get_all_evaluations,
    get_all_personalized_evaluations,
    get_all_properties,
    get_connection,
    get_distinct_cities,
    get_favorite_list_names,
    get_latest_evaluation,
    get_latest_personalized_evaluation,
    get_profile,
    get_properties_for_list,
    get_property,
    request_regeneration,
    save_profile,
)


def _inline(text: str) -> str:
    """Convert inline markdown (bold, italic) to HTML.

    Args:
        text: A single line of markdown text.

    Returns:
        The line with inline formatting converted to HTML tags.
    """
    # Escape HTML first.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic: *text*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


def _is_sold(status: str | None) -> bool:
    """Check if a property status indicates it has been sold.

    Args:
        status: Listing status text, or None.

    Returns:
        True if the status contains 'SOLD'.
    """
    return bool(status and "SOLD" in status.upper())


_STATUS_ORDER = {
    "ACTIVE": 0,
    "COMING SOON": 1,
    "PENDING": 2,
    "CONTINGENT": 3,
    "UNDER CONTRACT": 4,
    "SOLD": 5,
}


def _status_sort_key(status: str | None) -> tuple[int, str]:
    """Return a sort key for a property status.

    Args:
        status: Listing status text, or None for active listings.

    Returns:
        A tuple of (order_rank, status_text) for sorting.
    """
    if not status:
        return (0, "")
    s = status.upper()
    for keyword, rank in _STATUS_ORDER.items():
        if keyword in s:
            return (rank, s)
    return (6, s)


def _sort_properties(
    properties: list, sort: str, order: str
) -> list:
    """Sort a list of property rows by the given column.

    Args:
        properties: List of sqlite3.Row objects.
        sort: Column name to sort by, or 'status' for status ordering.
        order: 'asc' or 'desc'.

    Returns:
        Sorted list.
    """
    reverse = order == "desc"

    if sort == "status":
        properties.sort(key=lambda p: _status_sort_key(p["status"]), reverse=reverse)
    elif sort == "ppsf":
        def _ppsf(p: dict) -> float:
            if p["price"] and p["sqft"]:
                return p["price"] / p["sqft"]
            return 0.0 if not reverse else float("inf")
        properties.sort(key=_ppsf, reverse=reverse)
    else:
        def _key(p: dict) -> tuple:
            try:
                val = p[sort]
            except (KeyError, IndexError):
                return (1, "")
            if val is None:
                return (1, "")
            return (0, val)
        properties.sort(key=_key, reverse=reverse)

    return properties


def create_app() -> Flask:
    """Create and configure the Flask application.

    Returns:
        A configured Flask app instance.
    """
    app = Flask(__name__)

    @app.template_filter("render_markdown")
    def render_markdown_filter(text: str) -> Markup:
        """Convert simple markdown to HTML.

        Handles headings, bold, lists, and paragraphs. Not a full markdown
        parser — just enough for Claude's evaluation output.

        Args:
            text: Markdown text.

        Returns:
            Safe HTML markup.
        """
        lines = text.split("\n")
        html_lines: list[str] = []
        in_list = False
        list_type = ""

        for line in lines:
            stripped = line.strip()

            # Close list if needed.
            if in_list and not re.match(r"^(\d+\.|[-*])\s", stripped):
                html_lines.append(f"</{list_type}>")
                in_list = False

            # Headings.
            if stripped.startswith("## "):
                html_lines.append(f"<h2>{_inline(stripped[3:])}</h2>")
            elif stripped.startswith("### "):
                html_lines.append(f"<h3>{_inline(stripped[4:])}</h3>")
            # Numbered list.
            elif re.match(r"^\d+\.\s", stripped):
                if not in_list or list_type != "ol":
                    if in_list:
                        html_lines.append(f"</{list_type}>")
                    html_lines.append("<ol>")
                    in_list = True
                    list_type = "ol"
                content = re.sub(r"^\d+\.\s*", "", stripped)
                html_lines.append(f"<li>{_inline(content)}</li>")
            # Bullet list.
            elif re.match(r"^[-*]\s", stripped):
                if not in_list or list_type != "ul":
                    if in_list:
                        html_lines.append(f"</{list_type}>")
                    html_lines.append("<ul>")
                    in_list = True
                    list_type = "ul"
                content = stripped[2:]
                html_lines.append(f"<li>{_inline(content)}</li>")
            # Empty line.
            elif not stripped:
                html_lines.append("")
            # Paragraph.
            else:
                html_lines.append(f"<p>{_inline(stripped)}</p>")

        if in_list:
            html_lines.append(f"</{list_type}>")

        return Markup("\n".join(html_lines))

    @app.template_filter("currency")
    def currency_filter(value: int | float | None) -> str:
        """Format a number as currency.

        Args:
            value: Dollar amount or None.

        Returns:
            Formatted string like '$1,234,567' or '-'.
        """
        if value is None:
            return "-"
        return f"${value:,.0f}"

    @app.template_filter("number")
    def number_filter(value: int | float | None) -> str:
        """Format a number with commas.

        Args:
            value: Number or None.

        Returns:
            Formatted string or '-'.
        """
        if value is None:
            return "-"
        if isinstance(value, float) and value != int(value):
            return f"{value:,.1f}"
        return f"{value:,.0f}"

    @app.template_filter("status_badge_class")
    def status_badge_class_filter(status: str | None) -> str:
        """Return a CSS class for the property status badge.

        Args:
            status: Listing status text (e.g. "SOLD", "PENDING").

        Returns:
            A CSS class string for the badge.
        """
        if not status:
            return "badge-green"
        s = status.upper()
        if "SOLD" in s:
            return "badge-red"
        if "PENDING" in s or "CONTINGENT" in s or "UNDER CONTRACT" in s:
            return "badge-orange"
        if "COMING SOON" in s:
            return "badge-blue"
        return "badge-gray"

    @app.route("/")
    def index() -> str:
        """Render the main dashboard with all properties.

        Returns:
            Rendered HTML page.
        """
        conn = get_connection()
        try:
            list_name = request.args.get("list")
            city = request.args.get("city")
            sort = request.args.get("sort", "updated_at")
            order = request.args.get("order", "desc")

            if list_name:
                properties = get_properties_for_list(conn, list_name)
            else:
                properties = get_all_properties(conn)

            if city:
                properties = [p for p in properties if p["city"] == city]

            properties = _sort_properties(list(properties), sort, order)

            # Split into active and sold.
            active_properties = [p for p in properties if not _is_sold(p["status"])]
            sold_properties = [p for p in properties if _is_sold(p["status"])]

            # Build evaluation lookups.
            all_evals = get_all_evaluations(conn)
            eval_map = {r["property_url"]: r for r in all_evals}
            all_personal = get_all_personalized_evaluations(conn)
            personal_map = {r["property_url"]: r for r in all_personal}
            has_profile = get_profile(conn) is not None

            lists = get_favorite_list_names(conn)
            cities = get_distinct_cities(conn)

            return render_template(
                "index.html",
                properties=properties,
                active_properties=active_properties,
                sold_properties=sold_properties,
                eval_map=eval_map,
                personal_map=personal_map,
                has_profile=has_profile,
                lists=lists,
                cities=cities,
                active_list=list_name,
                active_city=city,
                active_sort=sort,
                active_order=order,
                total_evaluated=len(eval_map),
            )
        finally:
            conn.close()

    @app.route("/property")
    def property_detail() -> str:
        """Render the detail page for a single property.

        Returns:
            Rendered HTML page.
        """
        url = request.args.get("url", "")
        conn = get_connection()
        try:
            prop = get_property(conn, url)
            evaluation = get_latest_evaluation(conn, url) if prop else None
            personalized = (
                get_latest_personalized_evaluation(conn, url)
                if prop else None
            )
            return render_template(
                "property.html",
                prop=prop,
                evaluation=evaluation,
                personalized=personalized,
            )
        finally:
            conn.close()

    @app.route("/profile")
    def profile() -> str:
        """Render the buyer profile editor.

        Returns:
            Rendered profile page.
        """
        conn = get_connection()
        try:
            row = get_profile(conn)
            return render_template("profile.html", profile=row)
        finally:
            conn.close()

    @app.route("/profile/save", methods=["POST"])
    def profile_save() -> str:
        """Save the buyer profile (called by auto-save JS).

        Returns:
            JSON response with save status.
        """
        conn = get_connection()
        try:
            data = request.get_json()
            save_profile(conn, data.get("preferences", ""))
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/regenerate", methods=["POST"])
    def regenerate() -> str:
        """Queue a property for personalized evaluation regeneration.

        Writes to the regenerate_queue table; the daemon picks it up.

        Returns:
            JSON response indicating the request was queued.
        """
        data = request.get_json()
        url = data.get("url", "")
        if not url:
            return jsonify({"ok": False, "error": "Missing url"}), 400

        conn = get_connection()
        try:
            request_regeneration(conn, url)
            return jsonify({"ok": True})
        finally:
            conn.close()

    return app


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the Flask development server.

    Args:
        host: Host to bind to.
        port: Port to listen on.
    """
    app = create_app()
    print(f"Homepickle web UI: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
