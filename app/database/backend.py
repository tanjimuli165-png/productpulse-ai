"""Strict storage mode selection and a small DB-API bridge for SQLite/Postgres."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
import re
import sqlite3
import ssl
import threading
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

# libpq-compatible TLS modes accepted for SUPABASE_DB_URL. "disable"/"allow"/"prefer" are rejected
# because they can silently downgrade to plaintext.
_ALLOWED_SSLMODES = ("require", "verify-ca", "verify-full")


class StorageConfigurationError(RuntimeError):
    """Raised when cloud settings are partial, malformed, or explicitly requested but absent."""


class StorageConnectionError(RuntimeError):
    """Raised when the configured cloud database is unreachable; never downgraded to local storage."""


@dataclass(frozen=True)
class StorageConfig:
    backend: str = "local"
    database_url: str = field(default="", repr=False)
    supabase_url: str = ""
    service_role_key: str = field(default="", repr=False)
    bucket: str = "digital-product-assets"


def _streamlit_secrets() -> dict[str, str]:
    try:
        import streamlit as st
        secrets = st.secrets
        return {name: str(secrets[name]).strip() for name in (
            "APP_STORAGE_BACKEND", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_DB_URL", "SUPABASE_STORAGE_BUCKET",
        ) if name in secrets and str(secrets[name]).strip()}
    except (FileNotFoundError, ImportError):
        # Streamlit raises when no secrets.toml exists; environment-only local use is valid.
        return {}


def load_storage_config() -> StorageConfig:
    """Resolve secrets then environment; partial cloud setup always fails closed."""
    values = _streamlit_secrets()
    for name in ("APP_STORAGE_BACKEND", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                 "SUPABASE_DB_URL", "SUPABASE_STORAGE_BUCKET"):
        value = os.getenv(name)
        if value is not None and value.strip():
            values[name] = value.strip()

    selected = values.get("APP_STORAGE_BACKEND", "").strip().lower()
    if selected not in {"", "local", "supabase"}:
        raise StorageConfigurationError("APP_STORAGE_BACKEND must be 'local' or 'supabase'.")
    names = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL")
    present = [name for name in names if values.get(name)]
    cloud_signal = bool(present or values.get("SUPABASE_STORAGE_BUCKET"))
    if selected == "local":
        return StorageConfig(backend="local")
    if selected == "supabase" and len(present) != len(names):
        missing = ", ".join(name for name in names if not values.get(name))
        raise StorageConfigurationError(f"Supabase mode requires complete server secrets; missing: {missing}.")
    if cloud_signal and len(present) != len(names):
        missing = ", ".join(name for name in names if not values.get(name))
        raise StorageConfigurationError(
            f"Partial Supabase configuration detected; refusing local fallback. Missing: {missing}. "
            "Complete the cloud settings or explicitly set APP_STORAGE_BACKEND=local."
        )
    if not present:
        return StorageConfig(backend="local")

    supabase_url = values["SUPABASE_URL"].rstrip("/")
    parsed = urlparse(supabase_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise StorageConfigurationError("SUPABASE_URL must be a valid HTTPS project URL.")
    db_url = values["SUPABASE_DB_URL"]
    db_parsed = urlparse(db_url)
    if db_parsed.scheme not in {"postgres", "postgresql"} or not db_parsed.hostname:
        raise StorageConfigurationError("SUPABASE_DB_URL must be a PostgreSQL connection URI.")
    if "sslmode=disable" in db_parsed.query.lower():
        raise StorageConfigurationError("Supabase Postgres connections must use TLS; sslmode=disable is not allowed.")
    _validated_sslmode(db_parsed.query)
    key = values["SUPABASE_SERVICE_ROLE_KEY"]
    bucket = values.get("SUPABASE_STORAGE_BUCKET", "digital-product-assets").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,62}", bucket):
        raise StorageConfigurationError("SUPABASE_STORAGE_BUCKET must be a valid lowercase bucket name.")
    return StorageConfig("supabase", db_url, supabase_url, key, bucket)


def _query_options(query: str) -> dict[str, str]:
    """Parse libpq-style URI options (first value wins, keys lower-cased)."""
    return {key.lower(): values[0] for key, values in parse_qs(query, keep_blank_values=False).items() if values}


def _validated_sslmode(query: str) -> str:
    mode = _query_options(query).get("sslmode", "require").strip().lower()
    if mode not in _ALLOWED_SSLMODES:
        raise StorageConfigurationError(
            "SUPABASE_DB_URL sslmode must be one of: " + ", ".join(_ALLOWED_SSLMODES) + "."
        )
    return mode


def _ssl_context(query: str) -> ssl.SSLContext:
    """Build a TLS context that follows libpq semantics for the requested sslmode.

    require    -> encrypted, server certificate NOT verified (libpq behaviour). Supabase database
                  certificates are issued by Supabase's own CA, which is not in public trust stores.
    verify-ca  -> certificate chain verified, hostname not checked.
    verify-full-> chain and hostname verified. Use ``sslrootcert=/path/to/supabase-ca.crt`` in the URI.
    """
    mode = _validated_sslmode(query)
    root = _query_options(query).get("sslrootcert")
    if root and not os.path.isfile(root):
        raise StorageConfigurationError("The sslrootcert file in SUPABASE_DB_URL was not found or is not readable.")
    if mode == "require":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False  # must be cleared before verify_mode can be CERT_NONE
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context
    context = ssl.create_default_context(cafile=root) if root else ssl.create_default_context()
    context.check_hostname = mode == "verify-full"
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


_schema_lock = threading.Lock()
_schema_done: set[str] = set()


def schema_key(name: str, database_url: str) -> str:
    """Stable per-database key that never stores the connection string itself."""
    return f"{name}:{hashlib.sha256(database_url.encode('utf-8')).hexdigest()[:16]}"


def run_once_per_process(key: str, action: Callable[[], None]) -> None:
    """Run cloud schema/RLS setup once per process; a failed run is retried on the next call."""
    with _schema_lock:
        if key in _schema_done:
            return
        action()
        _schema_done.add(key)


class _PostgresCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        # sqlite3 cursors are iterable; keep the adapter behaviour-compatible.
        return iter(self._cursor.fetchall())


class _PostgresConnection:
    """Adapt the project's small SQLite-style query surface to pg8000 (DB-API, ``%s`` placeholders)."""
    def __init__(self, raw: Any):
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._raw.rollback()
            else:
                self._raw.commit()
        finally:
            self._raw.close()

    def execute(self, sql: str, params=()):
        normalized = sql.strip()
        if normalized.upper().startswith("PRAGMA FOREIGN_KEYS="):
            return _PostgresCursor(_EmptyCursor())
        if normalized.upper().startswith("BEGIN IMMEDIATE"):
            return _PostgresCursor(_EmptyCursor())
        pragma = re.fullmatch(r"PRAGMA\s+table_info\(([A-Za-z0-9_]+)\)", normalized, re.I)
        if pragma:
            table = pragma.group(1)
            cur = self._raw.cursor()
            cur.execute(
                "SELECT ordinal_position, column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                (table,),
            )
            return _PostgresCursor(cur)
        cur = self._raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return _PostgresCursor(cur)


class _EmptyCursor:
    rowcount = -1

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def connect_database(path: str, *, cloud: bool = False):
    """Open the chosen store. Connection failures are intentionally never downgraded."""
    if not cloud:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    config = load_storage_config()
    if config.backend != "supabase":
        raise StorageConfigurationError("Cloud database connection requested without complete Supabase configuration.")
    try:
        import pg8000.dbapi
    except ImportError as exc:
        raise RuntimeError("Cloud mode needs pg8000; install the project's requirements.") from exc
    parsed = urlparse(config.database_url)
    context = _ssl_context(parsed.query)  # configuration problems surface as StorageConfigurationError
    try:
        raw = pg8000.dbapi.connect(
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=unquote(parsed.path.lstrip("/")),
            ssl_context=context,
            timeout=10,
        )
        return _PostgresConnection(raw)
    except Exception as exc:
        kind = exc.__class__.__name__
        # Only the exception class is reported: driver messages can echo host/user details.
        looks_tls = isinstance(exc, ssl.SSLError) or any(word in str(exc).lower() for word in ("certificate", "ssl", "tls"))
        hint = " TLS verification failed; check sslmode/sslrootcert in SUPABASE_DB_URL." if looks_tls else ""
        logger.error("Supabase Postgres connection failed (%s).%s", kind, hint)
        raise StorageConnectionError(
            f"Could not connect to the configured Supabase Postgres database ({kind}); local fallback is disabled.{hint}"
        ) from None


def selected_storage_config() -> StorageConfig:
    return load_storage_config()


def harden_cloud_tables(conn: Any, table_names: tuple[str, ...]) -> None:
    """Enable RLS and deny direct browser roles; service credentials stay server-side."""
    if any(not re.fullmatch(r"[a-z_]+", table) for table in table_names):
        raise ValueError("Unsafe table identifier.")
    for table in table_names:
        conn.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        conn.execute(f"REVOKE ALL ON public.{table} FROM anon, authenticated")
        conn.execute(f"GRANT ALL ON public.{table} TO service_role")
