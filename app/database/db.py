import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import DB_PATH
from app.database.backend import (
    connect_database, harden_cloud_tables, load_storage_config, run_once_per_process, schema_key,
)
from app.database.models import Report


_HASH_ITERATIONS = 310_000


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, TypeError):
        return False


class ReportStore:
    """Account and report persistence; default mode is strict cloud-or-local."""
    def __init__(self, path: Path | str | None = None):
        config = load_storage_config()
        self.cloud = path is None and config.backend == "supabase"
        self.path = str(path or (config.database_url if self.cloud else DB_PATH))
        self.config = config
        self._init()

    def _connect(self):
        return connect_database(self.path, cloud=self.cloud)

    def _init(self):
        # Cloud DDL + RLS/GRANT statements take table locks, so run them once per process,
        # not on every Streamlit rerun. Local SQLite setup is cheap and stays per instance.
        if self.cloud:
            run_once_per_process(schema_key("reports", self.path), self._create_schema)
        else:
            self._create_schema()

    def _create_schema(self):
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE, expires_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, topic TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '')")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(reports)").fetchall()}
            if "user_id" not in columns:
                conn.execute("ALTER TABLE reports ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS reports_user_created ON reports(user_id, created_at DESC)")
            if self.cloud:
                harden_cloud_tables(conn, ("users", "sessions", "reports"))

    def register_user(self, username: str, password: str) -> Tuple[bool, str]:
        normalized = username.strip().lower()
        if not 3 <= len(normalized) <= 40 or not normalized.replace("_", "").replace("-", "").isalnum():
            return False, "Use 3–40 letters, numbers, underscores, or hyphens for the username."
        if len(password) < 8:
            return False, "Password must contain at least 8 characters."
        user_id = secrets.token_hex(16)
        try:
            with self._connect() as conn:
                conn.execute("INSERT INTO users (user_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                             (user_id, normalized, _password_hash(password), datetime.now(timezone.utc).isoformat()))
            return True, "Account created. You can now log in."
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) or exc.__class__.__name__ in {"IntegrityError", "UniqueViolation"}:
                return False, "That username is already registered."
            raise

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        normalized = username.strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT user_id, username, password_hash FROM users WHERE username = ?", (normalized,)).fetchone()
        if not row or not _password_matches(password, row[2]):
            return None
        return {"user_id": row[0], "username": row[1]}

    def create_session(self, user_id: str, days: int = 30) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user_id, expires_at))
        return token

    def authenticate_session(self, token: str) -> Optional[dict]:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute("SELECT users.user_id, users.username, sessions.expires_at FROM sessions JOIN users ON users.user_id = sessions.user_id WHERE sessions.token_hash = ?", (token_hash,)).fetchone()
        if not row:
            return None
        try:
            expiry = datetime.fromisoformat(row[2])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                self.revoke_session(token)
                return None
        except ValueError:
            return None
        return {"user_id": row[0], "username": row[1]}

    def revoke_session(self, token: str) -> None:
        token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def save(self, report: Report, user_id: str | None = None):
        owner = user_id or report.user_id or "legacy"
        report.user_id = owner
        with self._connect() as conn:
            # The conflict branch only updates a row that already belongs to the same owner, so an
            # existing report id can never be overwritten or re-assigned by another account.
            cursor = conn.execute(
                "INSERT INTO reports (id, topic, created_at, payload, user_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET topic=excluded.topic, created_at=excluded.created_at, payload=excluded.payload "
                "WHERE reports.user_id = excluded.user_id",
                (report.id, report.topic, report.created_at.isoformat(), report.model_dump_json(), owner),
            )
            if cursor.rowcount == 0:
                raise PermissionError("This report belongs to another account.")

    def get(self, report_id: str, user_id: str | None = None) -> Optional[Report]:
        if self.cloud and not user_id:
            raise ValueError("Cloud report reads must include the authenticated owner ID.")
        with self._connect() as conn:
            if user_id:
                row = conn.execute("SELECT payload FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id)).fetchone()
            else:
                row = conn.execute("SELECT payload FROM reports WHERE id = ?", (report_id,)).fetchone()
        return Report.model_validate_json(row[0]) if row else None

    def recent(self, user_id: str | None = None, limit: int = 10) -> List[Report]:
        if self.cloud and not user_id:
            raise ValueError("Cloud report history must include the authenticated owner ID.")
        bounded_limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            if user_id:
                rows = conn.execute("SELECT payload FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, bounded_limit)).fetchall()
            else:
                rows = conn.execute("SELECT payload FROM reports ORDER BY created_at DESC LIMIT ?", (bounded_limit,)).fetchall()
        return [Report.model_validate_json(row[0]) for row in rows]

    def export_reports_for_user(self, user_id: str) -> List[Report]:
        """Return every report owned by one explicitly identified user."""
        if not user_id or not user_id.strip():
            raise ValueError("A user ID is required for a private report export.")
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM reports WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [Report.model_validate_json(row[0]) for row in rows]
