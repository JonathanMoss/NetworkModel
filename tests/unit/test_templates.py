"""Unit tests for backend/template_renderer.py and modular HTML template integrity."""

import os
import re

import pytest

import template_renderer as tr

pytestmark = pytest.mark.unit


def test_render_base_template_resolves_all_partials():
    """base.html recursively includes all partials with no unresolved include tags."""
    html = tr.render_template("base.html")
    assert "<!DOCTYPE html>" in html
    assert "{% include" not in html
    for expected_id in [
        'id="ingest-overlay"',
        'id="view-switcher"',
        'id="left-panel"',
        'id="label-controls-master"',
        'id="tps-layers-panel"',
        'id="nwr-layers-panel"',
        'id="tps-hubs-panel"',
        'id="nwr-hubs-panel"',
        'id="dual-view-container"',
        'id="profile-drawer"',
        'id="inspector-drawer"',
        'id="catalog-modal"',
        'id="timing-modal"',
    ]:
        assert expected_id in html, f"Missing expected DOM section {expected_id}"


def test_all_linked_css_and_js_modules_exist_on_disk():
    """Every CSS and JS file linked in base.html exists under backend/static/ and is non-empty."""
    html = tr.render_template("base.html")
    static_root = os.path.abspath(os.path.join(tr.TEMPLATES_DIR, "..", "static"))

    css_links = re.findall(r'href="/static/([^"?]+)', html)
    js_links = re.findall(r'src="/static/([^"?]+)', html)

    assert len(css_links) >= 7
    assert len(js_links) >= 14

    for rel_path in css_links + js_links:
        full_path = os.path.join(static_root, rel_path)
        assert os.path.isfile(full_path), f"Missing static asset: {rel_path}"
        assert os.path.getsize(full_path) > 0, f"Empty static asset: {rel_path}"


def test_template_renderer_guards_path_traversal_and_cycles(tmp_path):
    """Rejects path traversal outside templates_dir, circular includes, and missing files."""
    with pytest.raises(ValueError, match="escapes templates directory"):
        tr.render_template("../main.py", templates_dir=str(tmp_path))

    with pytest.raises(FileNotFoundError):
        tr.render_template("nonexistent.html", templates_dir=str(tmp_path))

    (tmp_path / "a.html").write_text('{% include "b.html" %}', encoding="utf-8")
    (tmp_path / "b.html").write_text('{% include "a.html" %}', encoding="utf-8")
    with pytest.raises(ValueError, match="Circular template include"):
        tr.render_template("a.html", templates_dir=str(tmp_path))



def test_template_renderer_caches_and_resets():
    """Repeated render_template calls reuse cached HTML until reset_template_cache()."""
    tr.reset_template_cache()
    html1 = tr.render_template("base.html")
    html2 = tr.render_template("base.html")
    assert html1 == html2
    assert len(tr._TEMPLATE_CACHE) >= 1  # pylint: disable=protected-access
    tr.reset_template_cache()
    assert not tr._TEMPLATE_CACHE  # pylint: disable=protected-access
