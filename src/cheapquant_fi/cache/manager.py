"""FrameCache lifecycle, session persistence, and cached QuantLib entry points."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from datetime import date
from pathlib import Path

import polars as pl
from framecache import FrameCache
from framecache.backends import SQLiteBackend
from framecache.cache_config import CacheConfig

from cheapquant_fi.cache.registry import CacheRegistry
from cheapquant_fi.config import AppSettings, get_runtime_settings, get_settings
from cheapquant_fi.issuers import RateType
from cheapquant_fi.quantlib.cmt import ql_price_cmts


class CacheManager:
    """Owns the active cache DB, framecache instance, and session I/O."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self._backend = SQLiteBackend(self.settings.quant_cache_db_path)
        self._registry = CacheRegistry(self.settings.quant_cache_db_path)
        self._framecache = FrameCache(
            self._backend,
            framecache_key="cheapquant_fi",
            use_hash_keys=True,
            default_ttl=None,
        )
        self._session_id: str | None = None

    @property
    def framecache(self) -> FrameCache:
        return self._framecache

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def db_path(self) -> Path:
        return self.settings.quant_cache_db_path

    @property
    def use_quant_cache(self) -> bool:
        """Whether session results should be written to ``quant_cache_db`` (runtime toggle)."""
        return get_runtime_settings().use_quant_cache

    def price_cmts(
        self,
        source: str,
        valuation_date: str | date,
        rate_type: RateType | str = RateType.ZERO,
    ) -> pl.DataFrame:
        """Price CMTs from ycs_data without writing results to the cache."""
        if isinstance(valuation_date, date):
            valuation_date = valuation_date.isoformat()
        if isinstance(rate_type, RateType):
            rate_type = rate_type.value

        db_path = str(self.settings.ycs_db_path)
        return ql_price_cmts(db_path, source, valuation_date, rate_type=rate_type)

    def list_cache_entries(self) -> pl.DataFrame:
        return self._backend.metadata_df()

    def save_session(self, session_id: str | None = None) -> str:
        """Persist the active cache DB to sessions/{session_id}.db."""
        session_id = session_id or uuid.uuid4().hex[:12]
        dest = self.settings.sessions_dir / f"{session_id}.db"
        self._backend.close()
        self._registry.close()
        shutil.copy2(self.settings.quant_cache_db_path, dest)
        self._session_id = session_id
        self._reopen()
        return session_id

    def load_session(self, session_id: str) -> None:
        """Replace the active cache with a saved session."""
        src = self.settings.sessions_dir / f"{session_id}.db"
        if not src.exists():
            raise FileNotFoundError(f"No saved session {session_id!r} at {src}")
        self._backend.close()
        self._registry.close()
        shutil.copy2(src, self.settings.quant_cache_db_path)
        self._session_id = session_id
        self._reopen()
        self._framecache.refresh()

    def reset_cache(self) -> None:
        """Clear analytics tables and framecache entries."""
        self._backend.close()
        self._registry.close()
        if self.settings.quant_cache_db_path.exists():
            self.settings.quant_cache_db_path.unlink()
        self._session_id = None
        self._reopen()
        self._registry.reset_analytics_tables()

    def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self.settings.sessions_dir.glob("*.db"))

    def _reopen(self) -> None:
        self._backend = SQLiteBackend(self.settings.quant_cache_db_path)
        self._registry = CacheRegistry(self.settings.quant_cache_db_path)
        self._framecache = FrameCache(
            self._backend,
            framecache_key="cheapquant_fi",
            use_hash_keys=True,
            default_ttl=None,
        )

    def close(self) -> None:
        self._backend.close()
        self._registry.close()

    @classmethod
    def from_yaml(cls, path: Path | str, settings: AppSettings | None = None) -> "CacheManager":
        """Alternative constructor using a framecache YAML config."""
        config = CacheConfig.from_yaml(path)
        mgr = cls(settings=settings)
        mgr._backend.close()
        mgr._framecache = FrameCache.from_config(config)
        return mgr

    @staticmethod
    def backup_db(src: Path, dest: Path) -> None:
        """SQLite online backup (used internally by save/load)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
            source.backup(target)
