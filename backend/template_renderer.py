"""Lightweight recursive HTML template renderer for modular frontend partials.

Composes ``backend/templates/base.html`` and its ``{% include "partials/..." %}``
directives server-side so every tab, view, panel, drawer, and modal lives in
its own dedicated HTML template file with zero client-side layout shift and no
extra runtime dependencies.
"""

import os
import re
from typing import Optional

_INCLUDE_RE = re.compile(r'\{%\s*include\s+"([^"]+)"\s*%\}')

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# In-memory cache keyed by (base_dir, template_name) -> (mtime_signature, html)
_TEMPLATE_CACHE: dict[tuple[str, str], tuple[float, str]] = {}


def reset_template_cache() -> None:
    """Clear cached rendered templates (primarily for isolated unit tests)."""
    _TEMPLATE_CACHE.clear()


def _render_recursive(
    template_name: str,
    base_dir: str,
    seen: frozenset[str],
    mtimes: list[float],
) -> str:
    """Recursively resolve template includes while collecting file mtimes."""
    target_path = os.path.abspath(os.path.join(base_dir, template_name))

    # Guard against path traversal outside the templates root.
    if not (target_path == base_dir or target_path.startswith(base_dir + os.sep)):
        raise ValueError(f"Template path escapes templates directory: {template_name}")

    if template_name in seen:
        raise ValueError(f"Circular template include detected: {template_name}")

    mtimes.append(os.path.getmtime(target_path))
    with open(target_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    next_seen = seen | {template_name}

    def _replace(match: re.Match[str]) -> str:
        partial_name = match.group(1).strip()
        return _render_recursive(partial_name, base_dir, next_seen, mtimes)

    return _INCLUDE_RE.sub(_replace, content)


def _dir_max_mtime(base_dir: str) -> float:
    """Return the latest modification timestamp across all HTML files in ``base_dir``."""
    latest = 0.0
    for root, _dirs, files in os.walk(base_dir):
        for fname in files:
            if fname.endswith(".html"):
                mt = os.path.getmtime(os.path.join(root, fname))
                if mt > latest:
                    latest = mt
    return latest


def render_template(
    template_name: str,
    templates_dir: Optional[str] = None,
    _seen: Optional[frozenset[str]] = None,
) -> str:
    """Render an HTML template by recursively resolving ``{% include "..." %}`` tags.

    Uses an mtime-validated in-memory cache so repeated ``GET /`` requests avoid
    re-reading all partial files from disk while edits still auto-reload.
    """
    base_dir = os.path.abspath(templates_dir or TEMPLATES_DIR)
    cache_key = (base_dir, template_name)
    dir_mtime = _dir_max_mtime(base_dir)
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached is not None and cached[0] == dir_mtime:
        return cached[1]

    mtimes: list[float] = []
    html = _render_recursive(template_name, base_dir, _seen or frozenset(), mtimes)
    _TEMPLATE_CACHE[cache_key] = (max(mtimes, default=dir_mtime), html)
    return html

