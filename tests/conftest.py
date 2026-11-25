"""Shared Pytest fixtures and lightweight DB cursor doubles for isolated tests."""

from typing import Any, Callable, Optional

import pytest

import config
import timing_catalog
import track_identity
import track_matcher
from routers import nwr as nwr_router
from routers import tps as tps_router


class FakeCursor:
    """In-memory PostgreSQL cursor double for deterministic unit and E2E tests.

    Supports either a sequential queue of result lists or a dynamic callback
    ``handler(sql, params) -> list[dict] | dict | None``.
    """

    def __init__(
        self,
        responses: Optional[list[Any]] = None,
        handler: Optional[Callable[[str, Any], Any]] = None,
    ) -> None:
        self._responses = list(responses or [])
        self._handler = handler
        self._current: list[Any] = []
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        """Record the SQL execution and populate the current result buffer."""
        self.executed.append((sql, params))
        if self._handler is not None:
            result = self._handler(sql, params)
        elif self._responses:
            result = self._responses.pop(0)
        else:
            result = []

        if result is None:
            self._current = []
        elif isinstance(result, list):
            self._current = list(result)
        else:
            self._current = [result]

    def fetchone(self) -> Any:
        """Return and consume the next row, or ``None`` if exhausted."""
        return self._current.pop(0) if self._current else None

    def fetchall(self) -> list[Any]:
        """Return and clear all remaining rows in the current result set."""
        rows = list(self._current)
        self._current = []
        return rows

    def __iter__(self):
        return iter(self.fetchall())

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeConnection:
    """Context-manager wrapper returning a :class:`FakeCursor`."""

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self, row_factory: Any = None) -> FakeCursor:  # pylint: disable=unused-argument
        """Return the underlying fake cursor."""
        return self._cursor

    def commit(self) -> None:
        """Record that ``commit()`` was called."""
        self.committed = True

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_all_caches():
    """Reset all module-level in-memory caches before and after every test."""
    config.reset_caches()
    timing_catalog.reset_timing_catalog_cache()
    track_identity.reset_polarity_cache()
    track_matcher.reset_continuation_cache()
    tps_router.reset_overview_cache()
    nwr_router.reset_nwr_overview_cache()
    yield
    config.reset_caches()
    timing_catalog.reset_timing_catalog_cache()
    track_identity.reset_polarity_cache()
    track_matcher.reset_continuation_cache()
    tps_router.reset_overview_cache()
    nwr_router.reset_nwr_overview_cache()

