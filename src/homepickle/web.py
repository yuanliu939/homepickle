"""Flask web application for browsing property evaluations."""

import re

from flask import Flask, render_template, request
from markupsafe import Markup

from homepickle.storage import (
    get_all_evaluations,
    get_all_properties,
    get_connection,
    get_distinct_cities,
    get_favorite_list_names,
    get_latest_evaluation,
    get_properties_for_list,
    get_property,
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

            if list_name:
                properties = get_properties_for_list(conn, list_name)
            else:
                properties = get_all_properties(conn)

            if city:
                properties = [p for p in properties if p["city"] == city]

            # Build evaluation lookup.
            all_evals = get_all_evaluations(conn)
            eval_map = {r["property_url"]: r for r in all_evals}

            lists = get_favorite_list_names(conn)
            cities = get_distinct_cities(conn)

            return render_template(
                "index.html",
                properties=properties,
                eval_map=eval_map,
                lists=lists,
                cities=cities,
                active_list=list_name,
                active_city=city,
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
            return render_template(
                "property.html",
                prop=prop,
                evaluation=evaluation,
            )
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
