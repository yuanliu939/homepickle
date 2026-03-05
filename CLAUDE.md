# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Homepickle is a Python tool for analyzing saved homes from Redfin. It uses headless browser automation (Playwright) to scrape saved searches and surfaces property insights.

## Setup

```bash
uv sync                     # install all dependencies
uv run playwright install chromium
```

## Commands

```bash
uv run pytest                                 # run all tests
uv run pytest tests/test_foo.py::test_bar     # run a single test
uv run pytest -x                              # stop on first failure
uv run ruff check .                           # lint
uv run ruff format .                          # format
```

## Architecture

- `src/homepickle/` — main package (src layout, built with `uv_build`)
  - `models.py` — `Property` and `FavoriteList` dataclasses
  - `browser.py` — Playwright browser launch and Redfin login (cookie-based auth)
  - `scraper.py` — scrape favorites lists, property cards, and detail pages
  - `analyzer.py` — compute insights (price stats, city breakdown, value outliers)
  - `evaluator.py` — LLM evaluation via `claude -p` (non-interactive CLI)
  - `storage.py` — SQLite cache (`~/.homepickle/homepickle.db`) for properties, evaluations, and sync state
  - `__main__.py` — CLI entry point with commands: login, scrape, analyze, sync, evaluate, report, debug
- `tests/` — unit tests (mirror the package structure)
- `examples/` — generated debug output (gitignored)
- Data stored in `~/.homepickle/` (cookies.json, homepickle.db)

## Coding Standards

Follow [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html):

- Every function, class, and method must have a docstring.
- Every argument and return value must have a type annotation, including `None`.
- Use `ruff` for linting and formatting.

## Testing

- Write unit tests for common interfaces.
- Keep test cases simple and focused — do not over-complicate.

## Working Guidelines

- Update this CLAUDE.md when new insights or knowledge are discovered during R&D. Keep it clean and concise.
- Build Claude Code skills when reusable workflows emerge (see [skills docs](https://docs.anthropic.com/en/docs/claude-code/skills)).
