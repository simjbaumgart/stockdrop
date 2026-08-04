"""Load and render the snapshot's Jinja2 templates."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import jinja2

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=False,
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,  # surface missing keys instead of silent blanks
)


def render_package_readme(headline_stats: Dict[str, str]) -> str:
    template = _env.get_template("README.md.j2")
    return template.render(**headline_stats)


def render_data_readme(
    *, as_of: str, since_days: int, n_decisions: int, n_positions: int, n_summary: int
) -> str:
    template = _env.get_template("data_README.md.j2")
    return template.render(
        as_of=as_of,
        since_days=since_days,
        n_decisions=n_decisions,
        n_positions=n_positions,
        n_summary=n_summary,
    )
