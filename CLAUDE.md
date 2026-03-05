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
  - `models.py` — `Property` and `SavedSearch` dataclasses
  - `browser.py` — Playwright browser launch and Redfin login
  - `scraper.py` — scrape saved searches and property listings
  - `analyzer.py` — compute insights (price stats, medians)
- `tests/` — unit tests (mirror the package structure)
- `examples/` — generated structured JSON output (gitignored)

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
