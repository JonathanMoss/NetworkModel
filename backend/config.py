"""Application configuration, PostgreSQL connection factory, and shared caches.

This module centralizes environment-driven paths, database connections, the
in-memory TPS lookup-code cache, and persistence helpers for manual
TPS-to-NWR track overrides.
"""

import os
import threading
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

try:
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover
    ConnectionPool = None  # type: ignore[assignment,misc]

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
DB_POOL_MIN_SIZE: int = int(os.environ.get("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE: int = int(os.environ.get("DB_POOL_MAX_SIZE", "12"))

# ---------------------------------------------------------------------------
# Process-level lazy caches & connection pool (resettable in unit tests)
# ---------------------------------------------------------------------------
LOOKUP_CACHE: Optional[dict[str, dict[str, dict[str, Any]]]] = None
OVERRIDES_TABLE_READY: bool = False
PERF_INDEXES_READY: bool = False
POSSESSIONS_TABLES_READY: bool = False
DB_POOL: Optional[Any] = None
_DB_POOL_LOCK = threading.Lock()


def close_pool() -> None:
    """Close and clear the shared PostgreSQL connection pool if initialized."""
    global DB_POOL
    with _DB_POOL_LOCK:
        if DB_POOL is not None:
            DB_POOL.close()
            DB_POOL = None


def reset_caches() -> None:
    """Clear process-level caches and close any open connection pool.

    Intended for isolated unit tests so test cases never leak cached lookup
    tables, schema-readiness flags, or pooled connections across runs.
    """
    global LOOKUP_CACHE, OVERRIDES_TABLE_READY, PERF_INDEXES_READY, POSSESSIONS_TABLES_READY
    LOOKUP_CACHE = None
    OVERRIDES_TABLE_READY = False
    PERF_INDEXES_READY = False
    POSSESSIONS_TABLES_READY = False
    close_pool()


def get_pool() -> Optional[Any]:
    """Return the lazily initialized process-wide PostgreSQL ``ConnectionPool``.

    Returns ``None`` if ``psycopg_pool`` is unavailable or pool creation fails
    so callers can gracefully fall back to direct ``psycopg.connect()`` calls.
    """
    global DB_POOL
    if ConnectionPool is None:
        return None
    if DB_POOL is None:
        with _DB_POOL_LOCK:
            if DB_POOL is None:
                DB_POOL = ConnectionPool(
                    conninfo=DATABASE_URL,
                    min_size=DB_POOL_MIN_SIZE,
                    max_size=DB_POOL_MAX_SIZE,
                    open=True,
                )
    return DB_POOL


def get_conn() -> Any:
    """Return a context-managed PostgreSQL connection from the shared pool.

    Falls back to a direct ``psycopg.connect(DATABASE_URL)`` connection if
    connection pooling is unavailable.
    """
    pool = get_pool()
    if pool is not None:
        return pool.connection()
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


_PERFORMANCE_INDEX_DDLS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_signals_point_node ON signals (point_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_signals_id ON signals (id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_start_end ON edges (start_node_id, end_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_nwr_tcl_elr_meters ON nwr_track_centrelines (elr, start_meters, end_meters)",
)


def ensure_performance_indexes() -> None:
    """Idempotently create hot-path traversal and geo-lookup indexes once per process."""
    global PERF_INDEXES_READY
    if PERF_INDEXES_READY:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            for ddl in _PERFORMANCE_INDEX_DDLS:
                cur.execute(ddl)
        conn.commit()
    PERF_INDEXES_READY = True


def ensure_possessions_tables(cur) -> None:
    """Lazily create ``engineers_possessions`` and ``possession_worksites`` tables."""
    global POSSESSIONS_TABLES_READY
    if POSSESSIONS_TABLES_READY:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS engineers_possessions (
            id BIGSERIAL PRIMARY KEY,
            won_ref TEXT,
            title TEXT NOT NULL,
            picop_name TEXT,
            picop_contact TEXT,
            start_dt TEXT,
            end_dt TEXT,
            lines_blocked TEXT,
            ole_isolation TEXT NOT NULL DEFAULT 'N/A',
            status TEXT NOT NULL DEFAULT 'Planned',
            notes TEXT,
            color TEXT NOT NULL DEFAULT '#f59e0b',
            limit_mode TEXT NOT NULL DEFAULT 'signal_points',
            start_asset_type TEXT,
            start_asset_id BIGINT,
            start_label TEXT,
            start_qualifier TEXT NOT NULL DEFAULT 'at',
            end_asset_type TEXT,
            end_asset_id BIGINT,
            end_label TEXT,
            end_qualifier TEXT NOT NULL DEFAULT 'at',
            elr TEXT,
            start_meters INTEGER,
            end_meters INTEGER,
            track_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            lines JSONB NOT NULL DEFAULT '[]'::jsonb,
            render_style JSONB NOT NULL DEFAULT '{}'::jsonb,
            tps_geometry JSONB NOT NULL DEFAULT '{}'::jsonb,
            nwr_geometry JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    cur.execute(
        "ALTER TABLE engineers_possessions ADD COLUMN IF NOT EXISTS lines JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    cur.execute(
        "ALTER TABLE engineers_possessions ADD COLUMN IF NOT EXISTS render_style JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS possession_worksites (
            id BIGSERIAL PRIMARY KEY,
            possession_id BIGINT NOT NULL REFERENCES engineers_possessions(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            es_name TEXT,
            wmb_start_label TEXT,
            wmb_end_label TEXT,
            start_asset_type TEXT,
            start_asset_id BIGINT,
            end_asset_type TEXT,
            end_asset_id BIGINT,
            start_meters INTEGER,
            end_meters INTEGER,
            notes TEXT,
            tps_geometry JSONB NOT NULL DEFAULT '{}'::jsonb,
            nwr_geometry JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_possession_worksites_pid ON possession_worksites (possession_id)"
    )
    POSSESSIONS_TABLES_READY = True


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

