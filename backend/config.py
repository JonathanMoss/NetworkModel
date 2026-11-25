"""Application configuration, PostgreSQL connection factory, and shared caches.

This module centralizes environment-driven paths, database connections, the
in-memory TPS lookup-code cache, and persistence helpers for manual
TPS-to-NWR track overrides.
"""

import os
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Environment & filesystem paths
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://nr_user:nr_password@localhost:5433/network_rail_tps",
)
XML_PATH: str = os.environ.get("XML_PATH", "/data/tps")
NWR_DIR: str = os.environ.get("NWR_DIR", "/data/nwr")
NF_DIR: str = os.environ.get("NF_DIR", "/data/network_fusion")
SCHEMA_PATH: str = os.path.join(os.path.dirname(__file__), "schema.sql")

# ---------------------------------------------------------------------------
# Process-level lazy caches (resettable via reset_caches() in unit tests)
# ---------------------------------------------------------------------------
LOOKUP_CACHE: Optional[dict[str, dict[str, dict[str, Any]]]] = None
OVERRIDES_TABLE_READY: bool = False


def reset_caches() -> None:
    """Clear process-level caches in this module.

    Intended for isolated unit tests so test cases never leak cached lookup
    tables or schema-readiness flags across runs.
    """
    global LOOKUP_CACHE, OVERRIDES_TABLE_READY
    LOOKUP_CACHE = None
    OVERRIDES_TABLE_READY = False


def get_conn() -> psycopg.Connection:
    """Open and return a new PostgreSQL connection using ``DATABASE_URL``.

    Returns:
        A live ``psycopg.Connection`` ready for use as a context manager.
    """
    return psycopg.connect(DATABASE_URL)


def ensure_track_overrides_table(cur) -> None:
    """Lazily create the ``tps_nwr_track_overrides`` table if it does not exist.

    Uses a module-level readiness flag (``OVERRIDES_TABLE_READY``) to avoid
    issuing redundant DDL statements on every asset lookup.

    Args:
        cur: An active PostgreSQL cursor.
    """
    global OVERRIDES_TABLE_READY
    if OVERRIDES_TABLE_READY:
        return
    # Create the composite-keyed manual override table idempotently.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tps_nwr_track_overrides (
            asset_type TEXT NOT NULL,
            asset_id BIGINT NOT NULL,
            nwr_fid INTEGER NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (asset_type, asset_id)
        )
        """
    )
    OVERRIDES_TABLE_READY = True


def get_saved_track_override(cur, asset_type: str, asset_id: int) -> Optional[int]:
    """Look up a persisted manual NWR centreline ``fid`` override for a TPS asset.

    Signals and points share the same underlying ``signals.pk`` identifier space,
    so overrides saved under either ``'signal'`` or ``'points'`` are resolved
    interchangeably (preferring the most recently updated entry).

    Args:
        cur: An active PostgreSQL cursor returning dict rows.
        asset_type: Canonical TPS asset type (e.g. ``'signal'``, ``'points'``, ``'node'``).
        asset_id: Primary key of the TPS asset.

    Returns:
        The overridden NWR centreline ``fid`` as an ``int``, or ``None`` if no
        override exists (or if the query fails gracefully).
    """
    try:
        ensure_track_overrides_table(cur)
        if asset_type in ("signal", "points"):
            cur.execute(
                """
                SELECT nwr_fid FROM tps_nwr_track_overrides
                WHERE asset_type IN ('signal', 'points') AND asset_id = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (asset_id,),
            )
        else:
            cur.execute(
                """
                SELECT nwr_fid FROM tps_nwr_track_overrides
                WHERE asset_type = %s AND asset_id = %s
                LIMIT 1
                """,
                (asset_type, asset_id),
            )
        row = cur.fetchone()
        return int(row["nwr_fid"]) if row else None
    except Exception:
        return None


def load_lookup_cache() -> dict[str, dict[str, dict[str, Any]]]:
    """Load and cache all TPS ``lookup_codes`` rows grouped by category.

    Each cached entry stores its formatted ``display`` label alongside the raw
    ``label``, ``vanilla_text``, ``description``, and ``raw_attrs`` columns so
    callers can render human-friendly descriptions in O(1) time.

    Returns:
        Nested dict ``{category: {code_id_str: entry_dict}}``.
    """
    global LOOKUP_CACHE
    if LOOKUP_CACHE is not None:
        return LOOKUP_CACHE

    cache: dict[str, dict[str, dict[str, Any]]] = {}
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT category, code_id, label, vanilla_text, description, raw_attrs FROM lookup_codes"
            )
            for r in cur:
                cat = r["category"]
                if cat not in cache:
                    cache[cat] = {}
                # Prefer label, falling back through vanilla_text -> description -> raw code_id.
                display = r["label"] or r["vanilla_text"] or r["description"] or r["code_id"]
                if r["vanilla_text"] and r["label"] and r["vanilla_text"] != r["label"]:
                    display = f"{r['label']} ({r['vanilla_text']})"
                cache[cat][str(r["code_id"])] = {
                    "display": display,
                    "label": r["label"],
                    "vanilla_text": r["vanilla_text"],
                    "description": r["description"],
                    "raw_attrs": r["raw_attrs"],
                }

    LOOKUP_CACHE = cache
    return cache


def decode_code(category: str, code_id: Any) -> Optional[str]:
    """Decode a numeric/string TPS lookup code into its display string.

    Args:
        category: TPS lookup table name (e.g. ``'kmregionmasterdesc'``, ``'salinecodedesc'``).
        code_id: Raw code identifier (``int``, ``str``, or ``None``).

    Returns:
        Human-readable display string, or ``None`` if ``code_id`` is ``None`` or unknown.
    """
    if code_id is None:
        return None
    cache = load_lookup_cache()
    cat_map = cache.get(category, {})
    entry = cat_map.get(str(code_id))
    return entry["display"] if entry else None

