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


def render_template(
    template_name: str,
    templates_dir: Optional[str] = None,
    _seen: Optional[frozenset[str]] = None,
) -> str:
    """Render an HTML template by recursively resolving ``{% include "..." %}`` tags.

    Args:
        template_name: Relative path under ``templates_dir`` (e.g. ``"base.html"``).
        templates_dir: Optional override directory (defaults to :data:`TEMPLATES_DIR`).
        _seen: Internal set of active template names to guard against circular includes.

    Returns:
        Fully composed HTML string.

    Raises:
        FileNotFoundError: If ``template_name`` or any included partial does not exist.
        ValueError: If a path escapes ``templates_dir`` or a circular include is detected.
    """
    base_dir = os.path.abspath(templates_dir or TEMPLATES_DIR)
    target_path = os.path.abspath(os.path.join(base_dir, template_name))

    # Guard against path traversal outside the templates root.
    if not (target_path == base_dir or target_path.startswith(base_dir + os.sep)):
        raise ValueError(f"Template path escapes templates directory: {template_name}")

    seen = _seen or frozenset()
    if template_name in seen:
        raise ValueError(f"Circular template include detected: {template_name}")

    with open(target_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    next_seen = seen | {template_name}

    def _replace(match: re.Match[str]) -> str:
        partial_name = match.group(1).strip()
        return render_template(partial_name, templates_dir=base_dir, _seen=next_seen)

    return _INCLUDE_RE.sub(_replace, content)

