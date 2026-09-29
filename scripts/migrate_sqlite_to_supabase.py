#!/usr/bin/env python3
"""Idempotently copy one local SQLite application DB into configured Supabase storage."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database.backend import load_storage_config  # noqa: E402
from app.database.file_storage import LocalFileStorage  # noqa: E402
from app.database.db import ReportStore  # noqa: E402
from app.product.storage import ProductStore  # noqa: E402

TABLES = (
    "users", "sessions", "reports", "products", "product_versions",
    "product_content_versions", "product_assets", "product_qa_runs",
)


def _columns(conn, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _safe_filename(asset_id: str, mime: str) -> str:
    name = str(asset_id)
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in name):
        name = hashlib.sha256(name.encode()).hexdigest()
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg"}.get(mime, ".bin")
    return name + extension


def plan(source: Path) -> dict[str, int]:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source}")
    with sqlite3.connect(source) as src:
        result = {}
        existing = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            result[table] = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in existing else 0
        return result


def migrate(source: Path) -> dict[str, int]:
    config = load_storage_config()
    if config.backend != "supabase":
        raise RuntimeError("Set complete Supabase secrets and APP_STORAGE_BACKEND=supabase before --execute.")
    # Constructors apply the idempotent app schema and RLS hardening; no credentials are printed.
    reports = ReportStore()
    products = ProductStore()
    local_assets = LocalFileStorage(source.parent / "visual_assets")
    counts = {table: 0 for table in TABLES}
    with sqlite3.connect(source) as src:
        src.row_factory = sqlite3.Row
        existing = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in existing:
                continue
            source_columns = _columns(src, table)
            with reports._connect() as dst:
                target_columns = {row[1] for row in dst.execute(f"PRAGMA table_info({table})").fetchall()}
                columns = [column for column in source_columns if column in target_columns]
                if not columns:
                    continue
                quoted_cols = ",".join(f'"{name}"' for name in columns)
                placeholders = ",".join("?" for _ in columns)
                conflict = "ON CONFLICT DO NOTHING"
                for source_row in src.execute(f"SELECT {quoted_cols} FROM {table}"):
                    item = dict(source_row)
                    if table == "product_assets":
                        asset_id = str(item["asset_id"])
                        user_id = str(item["user_id"])
                        product_id = str(item["product_id"])
                        mime = str(item["mime_type"])
                        content = bytes(item.get("content") or b"")
                        if not content and item.get("storage_path"):
                            content = local_assets.get(item["storage_path"])
                        # Preserve already imported metadata and do not overwrite another row.
                        already = dst.execute("SELECT 1 FROM product_assets WHERE asset_id=?", (asset_id,)).fetchone()
                        if already:
                            continue
                        if content:
                            key = "/".join((quote(user_id, safe=""), quote(product_id, safe=""), _safe_filename(asset_id, mime)))
                            item["storage_path"] = products.visual_storage.put(key, content, mime, overwrite=True)
                        else:
                            item["storage_path"] = None
                        item["content"] = b""
                        if "storage_path" not in columns:
                            columns.append("storage_path")
                            quoted_cols = ",".join(f'"{name}"' for name in columns)
                            placeholders = ",".join("?" for _ in columns)
                        values = tuple(item.get(name) for name in columns)
                    else:
                        values = tuple(item.get(name) for name in columns)
                    inserted = dst.execute(
                        f"INSERT INTO {table} ({quoted_cols}) VALUES ({placeholders}) {conflict}", values
                    )
                    counts[table] += max(int(inserted.rowcount), 0)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=ROOT / "data" / "opportunities.db", help="source SQLite database")
    parser.add_argument("--execute", action="store_true", help="perform import (default is a local-only row-count plan)")
    args = parser.parse_args()
    counts = plan(args.sqlite)
    if not args.execute:
        print("Dry-run row counts (no Supabase connection made):")
        print(json.dumps(counts, indent=2))
        print("After a verified local backup, configure complete cloud secrets and rerun with --execute.")
        return
    print(json.dumps(migrate(args.sqlite), indent=2))
    print("Import complete. Counts are newly inserted rows; rerunning skips rows already present.")


if __name__ == "__main__":
    main()
