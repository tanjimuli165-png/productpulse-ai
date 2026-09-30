from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.database.backend import (
    connect_database, harden_cloud_tables, load_storage_config, run_once_per_process, schema_key,
)
from app.database.file_storage import LocalFileStorage, SupabasePrivateStorage
from app.product.product_schema import ProductDesign

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False)


class ProductStore:
    """User-scoped product persistence with local-file or private Supabase assets."""

    def __init__(self, path: Path | str | None = None):
        config = load_storage_config()
        self.cloud = path is None and config.backend == "supabase"
        self.config = config
        self.path = str(path or (config.database_url if self.cloud else DB_PATH))
        if not self.cloud:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.visual_storage = LocalFileStorage(Path(self.path).parent / "visual_assets")
        else:
            self.visual_storage = SupabasePrivateStorage(config)
        self._init()

    def _connect(self):
        return connect_database(self.path, cloud=self.cloud)

    def _init(self) -> None:
        # Cloud DDL + RLS/GRANT statements take table locks, so run them once per process.
        if self.cloud:
            run_once_per_process(schema_key("products", self.path), self._create_schema)
        else:
            self._create_schema()

    def _create_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source_report_id TEXT NOT NULL,
                    opportunity_index INTEGER NOT NULL,
                    opportunity_name TEXT NOT NULL,
                    source_payload TEXT NOT NULL,
                    inputs_payload TEXT NOT NULL,
                    blueprint_payload TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
            if "content_payload" not in product_columns:
                conn.execute("ALTER TABLE products ADD COLUMN content_payload TEXT")
            if "design_template_id" not in product_columns:
                conn.execute("ALTER TABLE products ADD COLUMN design_template_id TEXT NOT NULL DEFAULT 'minimal_professional'")
            if "page_size" not in product_columns:
                conn.execute("ALTER TABLE products ADD COLUMN page_size TEXT NOT NULL DEFAULT 'letter'")
            conn.execute("CREATE INDEX IF NOT EXISTS products_user_updated ON products(user_id, updated_at DESC)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS product_versions (
                    version_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    blueprint_payload TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(product_id, version_number),
                    FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS product_content_versions (
                    version_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    content_payload TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(product_id, version_number),
                    FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS product_assets (
                    asset_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    placement TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    content BYTEA NOT NULL,
                    created_at TEXT NOT NULL,
                    storage_path TEXT,
                    FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
                )
            """)
            asset_columns = {row[1] for row in conn.execute("PRAGMA table_info(product_assets)")}
            if "storage_path" not in asset_columns:
                conn.execute("ALTER TABLE product_assets ADD COLUMN storage_path TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS product_assets_owner ON product_assets(user_id, product_id, created_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS product_qa_runs (
                    run_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    snapshot_fingerprint TEXT NOT NULL,
                    result_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(product_id) REFERENCES products(product_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS product_qa_owner ON product_qa_runs(user_id, product_id, created_at DESC)")
            if self.cloud:
                # users/sessions/reports are created and hardened by ReportStore; hardening them here
                # would fail when ProductStore is the first store to touch a fresh database.
                harden_cloud_tables(conn, (
                    "products", "product_versions", "product_content_versions",
                    "product_assets", "product_qa_runs",
                ))

    @staticmethod
    def _decode(row: tuple | None) -> dict | None:
        if not row:
            return None
        keys = (
            "product_id", "user_id", "source_report_id", "opportunity_index", "opportunity_name",
            "source_payload", "inputs_payload", "blueprint_payload", "status", "created_at",
            "updated_at", "content_payload", "design_template_id", "page_size",
        )
        item = dict(zip(keys, row))
        item["source_payload"] = json.loads(item["source_payload"])
        item["inputs_payload"] = json.loads(item["inputs_payload"])
        item["blueprint_payload"] = json.loads(item["blueprint_payload"]) if item["blueprint_payload"] else None
        item["content_payload"] = json.loads(item["content_payload"]) if item["content_payload"] else None
        return item

    @staticmethod
    def _select_columns() -> str:
        return "product_id,user_id,source_report_id,opportunity_index,opportunity_name,source_payload,inputs_payload,blueprint_payload,status,created_at,updated_at,content_payload,design_template_id,page_size"

    def get(self, product_id: str, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._select_columns()} FROM products WHERE product_id=? AND user_id=?",
                (product_id, user_id),
            ).fetchone()
            product = self._decode(row)
            if product:
                versions = conn.execute(
                    "SELECT version_number,change_summary,created_at FROM product_versions WHERE product_id=? ORDER BY version_number",
                    (product_id,),
                ).fetchall()
                product["versions"] = [
                    {"version_number": v[0], "change_summary": v[1], "created_at": v[2]} for v in versions
                ]
                content_versions = conn.execute(
                    "SELECT version_number,change_summary,created_at FROM product_content_versions WHERE product_id=? ORDER BY version_number",
                    (product_id,),
                ).fetchall()
                product["content_versions"] = [
                    {"version_number": v[0], "change_summary": v[1], "created_at": v[2]}
                    for v in content_versions
                ]
            return product

    def recent_for_opportunity(self, user_id: str, report_id: str, opportunity_index: int, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._select_columns()} FROM products WHERE user_id=? AND source_report_id=? AND opportunity_index=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, report_id, opportunity_index, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def save(
        self,
        *,
        product_id: str,
        user_id: str,
        source_report_id: str,
        opportunity_index: int,
        opportunity_name: str,
        source_payload: dict,
        inputs_payload: dict,
        blueprint_payload: dict | None,
        status: str = "draft",
        change_summary: str | None = None,
        create_version: bool = False,
    ) -> dict:
        if not user_id:
            raise ValueError("A signed-in user is required to save a product blueprint.")
        if status not in {"draft", "approved"}:
            raise ValueError("Invalid product status.")
        now = _now()
        source_json = _dump(source_payload)
        inputs_json = _dump(inputs_payload)
        blueprint_json = _dump(blueprint_payload) if blueprint_payload is not None else None
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            existing = conn.execute(
                "SELECT user_id,blueprint_payload,content_payload FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
            if existing and existing[0] != user_id:
                raise PermissionError("This product blueprint belongs to another account.")
            if existing:
                blueprint_changed = blueprint_json != existing[1]
                inputs_changed = inputs_json != (
                    conn.execute(
                        "SELECT inputs_payload FROM products WHERE product_id=?",
                        (product_id,),
                    ).fetchone()[0] or "{}"
                )
                invalidate_content = bool(existing[2]) and (blueprint_changed or inputs_changed)
                conn.execute(
                    "UPDATE products SET source_report_id=?,opportunity_index=?,opportunity_name=?,source_payload=?,inputs_payload=?,blueprint_payload=?,status=?,updated_at=?,content_payload=CASE WHEN ?=1 THEN NULL ELSE content_payload END WHERE product_id=? AND user_id=?",
                    (source_report_id, opportunity_index, opportunity_name, source_json, inputs_json, blueprint_json, status, now, int(invalidate_content), product_id, user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO products(product_id,user_id,source_report_id,opportunity_index,opportunity_name,source_payload,inputs_payload,blueprint_payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (product_id, user_id, source_report_id, opportunity_index, opportunity_name, source_json, inputs_json, blueprint_json, status, now, now),
                )
            if create_version and blueprint_json:
                number = conn.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM product_versions WHERE product_id=?", (product_id,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO product_versions(version_id,product_id,version_number,blueprint_payload,change_summary,created_at) VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, product_id, number, blueprint_json, change_summary or "Blueprint saved", now),
                )
        return self.get(product_id, user_id)

    def save_content(
        self,
        *,
        product_id: str,
        user_id: str,
        content_payload: dict,
        change_summary: str,
    ) -> dict:
        """Save a content snapshot only for its owner's approved product blueprint."""
        if not user_id:
            raise ValueError("A signed-in user is required to save product content.")
        content_json = _dump(content_payload)
        now = _now()
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            product = conn.execute("SELECT user_id,status,blueprint_payload FROM products WHERE product_id=?", (product_id,)).fetchone()
            if not product:
                raise LookupError("The product draft could not be found.")
            if product[0] != user_id:
                raise PermissionError("This product content belongs to another account.")
            if product[1] != "approved":
                raise ValueError("Approve the product blueprint before saving generated content.")
            if not product[2]:
                raise ValueError("A saved approved blueprint is required before saving product content.")
            blueprint_fingerprint = hashlib.sha256(
                json.dumps(json.loads(product[2]), sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if json.loads(content_json).get("blueprint_fingerprint") != blueprint_fingerprint:
                raise ValueError("Product content must match the current approved blueprint revision.")
            conn.execute(
                "UPDATE products SET content_payload=?,updated_at=? WHERE product_id=? AND user_id=?",
                (content_json, now, product_id, user_id),
            )
            number = conn.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM product_content_versions WHERE product_id=?",
                (product_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO product_content_versions(version_id,product_id,version_number,content_payload,change_summary,created_at) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, product_id, number, content_json, change_summary[:300], now),
            )
        return self.get(product_id, user_id)

    def save_design(
        self,
        *,
        product_id: str,
        user_id: str,
        design: ProductDesign | dict,
    ) -> dict:
        """Save design settings only for the signed-in owner's existing product."""
        if not user_id:
            raise ValueError("A signed-in user is required to save product design settings.")
        validated = design if isinstance(design, ProductDesign) else ProductDesign.model_validate(design)
        with self._connect() as conn:
            product = conn.execute("SELECT user_id FROM products WHERE product_id=?", (product_id,)).fetchone()
            if not product:
                raise LookupError("The product draft could not be found.")
            if product[0] != user_id:
                raise PermissionError("These product design settings belong to another account.")
            conn.execute(
                "UPDATE products SET design_template_id=?,page_size=?,updated_at=? WHERE product_id=? AND user_id=?",
                (validated.template_id, validated.page_size, _now(), product_id, user_id),
            )
        return self.get(product_id, user_id)

    def save_visual_asset(
        self,
        *,
        product_id: str,
        user_id: str,
        asset_type: str,
        title: str,
        filename: str,
        mime_type: str,
        placement: str,
        metadata: dict,
        content: bytes,
    ) -> dict:
        """Persist one bounded visual asset for the authenticated product owner."""
        if not user_id:
            raise ValueError("A signed-in user is required to save product visuals.")
        if asset_type not in {"icon", "shape", "diagram", "upload"}:
            raise ValueError("Unsupported product visual type.")
        if mime_type not in {"image/svg+xml", "image/png", "image/jpeg"}:
            raise ValueError("Unsupported product visual image format.")
        if (asset_type == "upload") != (mime_type in {"image/png", "image/jpeg"}):
            raise ValueError("Uploaded and generated visual formats do not match their asset type.")
        if not isinstance(content, bytes) or not content or len(content) > 2 * 1024 * 1024:
            raise ValueError("Product visuals must contain between 1 byte and 2 MB of image data.")
        title = " ".join(str(title or "").split())
        filename = Path(str(filename or "visual").replace("\\", "/")).name
        if not title or len(title) > 120 or len(filename) > 120:
            raise ValueError("Visual titles and filenames must be valid and no longer than 120 characters.")
        if placement != "cover" and not re.fullmatch(r"section:(?:[0-9]|1[01])", placement or ""):
            raise ValueError("Choose the cover or one of the product sections for this visual.")
        now = _now()
        asset_id = uuid.uuid4().hex
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg"}[mime_type]
        from urllib.parse import quote
        storage_key = "/".join((quote(str(user_id), safe=""), quote(str(product_id), safe=""), asset_id + extension))
        stored_key = self.visual_storage.put(storage_key, content, mime_type)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                ownership_query = "SELECT user_id FROM products WHERE product_id=?" + (" FOR UPDATE" if self.cloud else "")
                product = conn.execute(ownership_query, (product_id,)).fetchone()
                if not product:
                    raise LookupError("The product draft could not be found.")
                if product[0] != user_id:
                    raise PermissionError("These product visuals belong to another account.")
                count = conn.execute(
                    "SELECT COUNT(*) FROM product_assets WHERE product_id=? AND user_id=?",
                    (product_id, user_id),
                ).fetchone()[0]
                if count >= 12:
                    raise ValueError("This product already has the maximum of 12 saved visuals. Remove one before adding another.")
                conn.execute(
                    "INSERT INTO product_assets(asset_id,product_id,user_id,asset_type,title,filename,mime_type,placement,metadata_json,content,created_at,storage_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (asset_id, product_id, user_id, asset_type, title, filename, mime_type, placement, _dump(metadata), b"", now, stored_key),
                )
        except Exception:
            self._discard_object(stored_key)
            raise
        return {
            "asset_id": asset_id, "product_id": product_id, "user_id": user_id,
            "asset_type": asset_type, "title": title, "filename": filename,
            "mime_type": mime_type, "placement": placement, "metadata": metadata,
            "created_at": now,
        }

    def list_visual_assets(self, product_id: str, user_id: str) -> list[dict]:
        """Return only metadata visible to the authenticated product owner."""
        if not user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT asset_id,product_id,user_id,asset_type,title,filename,mime_type,placement,metadata_json,created_at "
                "FROM product_assets WHERE product_id=? AND user_id=? ORDER BY created_at,asset_id",
                (product_id, user_id),
            ).fetchall()
        keys = ("asset_id", "product_id", "user_id", "asset_type", "title", "filename", "mime_type", "placement", "metadata_json", "created_at")
        assets = []
        for row in rows:
            item = dict(zip(keys, row))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            assets.append(item)
        return assets

    def get_visual_assets(self, product_id: str, user_id: str) -> list[dict]:
        """Load image bytes only for assets belonging to the authenticated owner."""
        if not user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT asset_id,product_id,user_id,asset_type,title,filename,mime_type,placement,metadata_json,content,created_at,storage_path "
                "FROM product_assets WHERE product_id=? AND user_id=? ORDER BY created_at,asset_id",
                (product_id, user_id),
            ).fetchall()
        keys = ("asset_id", "product_id", "user_id", "asset_type", "title", "filename", "mime_type", "placement", "metadata_json", "content", "created_at", "storage_path")
        assets = []
        for row in rows:
            item = dict(zip(keys, row))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            legacy_bytes = bytes(item.pop("content") or b"")
            storage_path = item.pop("storage_path")
            if storage_path:
                item["content"] = self.visual_storage.get(storage_path)
            elif legacy_bytes:
                # Upgrade older local databases whose visuals were stored as BLOBs.
                extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg"}.get(item["mime_type"], ".bin")
                from urllib.parse import quote
                storage_path = "/".join((quote(str(user_id), safe=""), quote(str(product_id), safe=""), item["asset_id"] + extension))
                self.visual_storage.put(storage_path, legacy_bytes, item["mime_type"])
                with self._connect() as conn:
                    conn.execute("UPDATE product_assets SET storage_path=?,content=? WHERE asset_id=? AND product_id=? AND user_id=?", (storage_path, b"", item["asset_id"], product_id, user_id))
                item["content"] = legacy_bytes
            else:
                raise RuntimeError("Saved visual metadata has no private file; restore from backup before use.")
            assets.append(item)
        return assets

    def delete_visual_asset(self, *, product_id: str, user_id: str, asset_id: str) -> bool:
        """Delete only the named visual attached to this owner's product."""
        if not user_id:
            raise ValueError("A signed-in user is required to remove product visuals.")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT storage_path FROM product_assets WHERE asset_id=? AND product_id=? AND user_id=?",
                (asset_id, product_id, user_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM product_assets WHERE asset_id=? AND product_id=? AND user_id=?",
                (asset_id, product_id, user_id),
            )
        if row[0]:
            # The database row is already gone, so the asset is removed from the app either way.
            self._discard_object(row[0])
        return True

    def _discard_object(self, storage_key: str) -> None:
        """Best-effort object removal (one retry). Never raises, so it cannot mask the caller's outcome."""
        for attempt in (1, 2):
            try:
                self.visual_storage.delete(storage_key)
                return
            except Exception as exc:  # noqa: BLE001 - storage backends raise several error types
                if attempt == 2:
                    logger.warning(
                        "Private visual object %r could not be removed (%s); it is orphaned and needs manual cleanup.",
                        storage_key, exc.__class__.__name__,
                    )

    def save_qa_run(
        self,
        *,
        product_id: str,
        user_id: str,
        snapshot_fingerprint: str,
        result_payload: dict,
    ) -> dict:
        """Save a QA result only for the authenticated owner's approved, saved content."""
        if not user_id:
            raise ValueError("A signed-in user is required to save product QA results.")
        if not re.fullmatch(r"[a-f0-9]{64}", snapshot_fingerprint or ""):
            raise ValueError("A valid QA snapshot fingerprint is required.")
        serialized = _dump(result_payload)
        if len(serialized.encode("utf-8")) > 1_000_000:
            raise ValueError("QA result is too large to persist safely.")
        now = _now()
        run_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            product = conn.execute(
                "SELECT user_id,status,content_payload FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
            if not product:
                raise LookupError("The product draft could not be found.")
            if product[0] != user_id:
                raise PermissionError("These product QA results belong to another account.")
            if product[1] != "approved" or not product[2]:
                raise ValueError("Approve the blueprint and save product content before running QA.")
            conn.execute(
                "INSERT INTO product_qa_runs(run_id,product_id,user_id,snapshot_fingerprint,result_payload,created_at) VALUES(?,?,?,?,?,?)",
                (run_id, product_id, user_id, snapshot_fingerprint, serialized, now),
            )
        return {
            "run_id": run_id,
            "product_id": product_id,
            "user_id": user_id,
            "snapshot_fingerprint": snapshot_fingerprint,
            "result_payload": result_payload,
            "created_at": now,
        }

    def list_qa_runs(self, product_id: str, user_id: str, limit: int = 10) -> list[dict]:
        """Return only the signed-in owner's QA history for this product."""
        if not user_id:
            return []
        bounded_limit = max(1, min(int(limit), 50))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id,product_id,user_id,snapshot_fingerprint,result_payload,created_at "
                "FROM product_qa_runs WHERE product_id=? AND user_id=? ORDER BY created_at DESC,run_id DESC LIMIT ?",
                (product_id, user_id, bounded_limit),
            ).fetchall()
        return [
            {
                "run_id": row[0],
                "product_id": row[1],
                "user_id": row[2],
                "snapshot_fingerprint": row[3],
                "result_payload": json.loads(row[4]),
                "created_at": row[5],
            }
            for row in rows
        ]

    def export_user_data(self, user_id: str) -> list[dict]:
        """Export full products, histories, QA runs, and bytes for one explicit owner."""
        if not user_id or not user_id.strip():
            raise ValueError("A signed-in user ID is required for product export.")
        with self._connect() as conn:
            product_ids = [row[0] for row in conn.execute(
                "SELECT product_id FROM products WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()]
        exported = []
        for product_id in product_ids:
            product = self.get(product_id, user_id)
            with self._connect() as conn:
                blueprints = conn.execute(
                    "SELECT version_number,blueprint_payload,change_summary,created_at FROM product_versions WHERE product_id=? ORDER BY version_number",
                    (product_id,),
                ).fetchall()
                contents = conn.execute(
                    "SELECT version_number,content_payload,change_summary,created_at FROM product_content_versions WHERE product_id=? ORDER BY version_number",
                    (product_id,),
                ).fetchall()
                qa = conn.execute(
                    "SELECT run_id,snapshot_fingerprint,result_payload,created_at FROM product_qa_runs WHERE product_id=? AND user_id=? ORDER BY created_at,run_id",
                    (product_id, user_id),
                ).fetchall()
            product.pop("versions", None)
            product.pop("content_versions", None)
            exported.append({
                "product": product,
                "blueprint_versions": [
                    {"version_number": r[0], "blueprint_payload": json.loads(r[1]), "change_summary": r[2], "created_at": r[3]}
                    for r in blueprints
                ],
                "content_versions": [
                    {"version_number": r[0], "content_payload": json.loads(r[1]), "change_summary": r[2], "created_at": r[3]}
                    for r in contents
                ],
                "qa_runs": [
                    {"run_id": r[0], "snapshot_fingerprint": r[1], "result_payload": json.loads(r[2]), "created_at": r[3]}
                    for r in qa
                ],
                "visual_assets": self.get_visual_assets(product_id, user_id),
            })
        return exported
