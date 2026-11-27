"""Unit tests for backend/config.py."""

import pytest

import config
from tests.conftest import FakeConnection, FakeCursor


pytestmark = pytest.mark.unit


def test_ensure_track_overrides_table_runs_once():
    """DDL runs on first call and is memoized on subsequent calls."""
    cur = FakeCursor()
    config.ensure_track_overrides_table(cur)
    config.ensure_track_overrides_table(cur)
    assert len(cur.executed) == 1
    assert "CREATE TABLE IF NOT EXISTS tps_nwr_track_overrides" in cur.executed[0][0]


def test_get_saved_track_override_signal_and_edge():
    """Signal/points queries check both types; edge queries check exact type."""
    cur = FakeCursor(responses=[[], [{"nwr_fid": 42}], [{"nwr_fid": 99}], []])
    assert config.get_saved_track_override(cur, "signal", 1001) == 42
    assert "IN ('signal', 'points')" in cur.executed[1][0]

    assert config.get_saved_track_override(cur, "edge", 2002) == 99
    assert "WHERE asset_type = %s" in cur.executed[2][0]

    assert config.get_saved_track_override(cur, "node", 3003) is None


def test_get_saved_track_override_handles_db_error():
    """Database errors during override lookup degrade gracefully to None."""

    class BoomCursor:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    assert config.get_saved_track_override(BoomCursor(), "signal", 1) is None


def test_load_lookup_cache_and_decode_code(monkeypatch):
    """Lookup cache formats combined label/vanilla_text and caches results."""
    rows = [
        {
            "category": "kmregionmasterdesc",
            "code_id": "10",
            "label": "MLN1",
            "vanilla_text": "MLN1",
            "description": "Great Western Main Line",
            "raw_attrs": {},
        },
        {
            "category": "signaltype",
            "code_id": "2",
            "label": "Main",
            "vanilla_text": "4-Aspect",
            "description": "Main running signal",
            "raw_attrs": {"aspects": 4},
        },
    ]
    cur = FakeCursor(responses=[rows])
    calls = {"n": 0}

    def fake_get_conn():
        calls["n"] += 1
        return FakeConnection(cur)

    monkeypatch.setattr(config, "get_conn", fake_get_conn)

    cache1 = config.load_lookup_cache()
    cache2 = config.load_lookup_cache()
    assert cache1 is cache2
    assert calls["n"] == 1

    assert config.decode_code("kmregionmasterdesc", 10) == "MLN1"
    assert config.decode_code("signaltype", 2) == "Main (4-Aspect)"
    assert config.decode_code("signaltype", 999) is None
    assert config.decode_code("signaltype", None) is None


def test_get_conn_uses_pool_and_closes_cleanly(monkeypatch):
    """get_conn acquires a pooled connection when psycopg_pool is available."""
    created = {}

    class FakePool:
        def __init__(self, conninfo, min_size, max_size, open=True):
            created["conninfo"] = conninfo
            created["min_size"] = min_size
            created["max_size"] = max_size
            created["open"] = open
            self.closed = False

        def connection(self):
            return "pooled-conn"

        def close(self):
            self.closed = True

    monkeypatch.setattr(config, "ConnectionPool", FakePool)
    assert config.get_conn() == "pooled-conn"
    assert created["conninfo"] == config.DATABASE_URL
    pool_ref = config.DB_POOL
    assert pool_ref is not None
    config.close_pool()
    assert pool_ref.closed is True
    assert config.DB_POOL is None


def test_get_conn_falls_back_to_psycopg_when_pool_unavailable(monkeypatch):
    """get_conn passes DATABASE_URL to psycopg.connect when ConnectionPool is None."""
    captured = {}

    def fake_connect(dsn):
        captured["dsn"] = dsn
        return "conn-obj"

    monkeypatch.setattr(config, "ConnectionPool", None)
    monkeypatch.setattr(config.psycopg, "connect", fake_connect)
    assert config.get_conn() == "conn-obj"
    assert captured["dsn"] == config.DATABASE_URL



def test_ensure_performance_indexes_runs_once(monkeypatch):
    """ensure_performance_indexes creates all 4 hot-path indexes once and memoizes."""
    cur = FakeCursor()
    monkeypatch.setattr(config, "get_conn", lambda: FakeConnection(cur))
    config.ensure_performance_indexes()
    config.ensure_performance_indexes()
    assert len(cur.executed) == len(config._PERFORMANCE_INDEX_DDLS)  # pylint: disable=protected-access
    assert any("idx_signals_point_node" in q[0] for q in cur.executed)
    assert any("idx_edges_start_end" in q[0] for q in cur.executed)
