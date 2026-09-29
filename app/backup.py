from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.database.db import ReportStore
from app.product.storage import ProductStore


EXCLUDED_PARTS = {
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".venv", "venv", ".env", ".streamlit",
    "visual_assets",  # Private binary files are exported only through the owner-scoped store below.
}
INCLUDED_ROOT_FILES = {"requirements.txt", "README.md"}
_REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_allowed(path: Path, base: Path) -> bool:
    relative_parts = set(path.relative_to(base).parts)
    return not path.is_symlink() and not relative_parts.intersection(EXCLUDED_PARTS) and path.is_file()


def _safe_name(value: str) -> str:
    if _REPORT_ID_PATTERN.fullmatch(value or ""):
        return value
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def create_user_backup(
    base_dir: Path,
    store: ReportStore,
    user_id: str,
    product_store: ProductStore | None = None,
) -> bytes:
    """Create an account-only source/data archive; never copy the shared live database."""
    if not user_id or not user_id.strip():
        raise ValueError("A signed-in user is required to create a private backup.")
    base_dir = Path(base_dir).resolve()
    reports = store.export_reports_for_user(user_id)
    product_store = product_store or ProductStore(None if store.cloud else store.path)
    products = product_store.export_user_data(user_id)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        app_dir = base_dir / "app"
        for path in sorted(app_dir.rglob("*")) if app_dir.exists() else []:
            if _is_allowed(path, base_dir):
                archive.write(path, path.relative_to(base_dir).as_posix())
        for path in sorted(base_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            if path.name in INCLUDED_ROOT_FILES or (path.name.startswith("v") and path.name.endswith("_smoke_test.py")):
                archive.write(path, path.relative_to(base_dir).as_posix())
        archive.writestr(
            "USER_BACKUP_README.txt",
            "Personal Digital Product Engine backup\n\n"
            "Contains application source, this account's report JSON/PDF exports, product drafts, "
            "blueprint/content versions, QA history, and private visual files. It does not contain "
            "the shared live database, account records, password hashes, session records, or other "
            "users' data. Internal user IDs are omitted from exported JSON. Keep this archive private.\n",
        )
        for report in reports:
            file_stem = _safe_name(report.id)
            report_data = report.model_dump(mode="json", exclude={"user_id"})
            archive.writestr(f"my_reports/{file_stem}.json", json.dumps(report_data, ensure_ascii=False, indent=2) + "\n")
            if not _REPORT_ID_PATTERN.fullmatch(report.id):
                continue
            report_dir = base_dir / "data" / "reports"
            if report_dir.is_symlink():
                continue
            pdf_path = report_dir / f"opportunity-report-{report.id}.pdf"
            try:
                report_root = report_dir.resolve(strict=True)
                report_root.relative_to(base_dir)
                resolved_pdf = pdf_path.resolve(strict=True)
                resolved_pdf.relative_to(report_root)
            except (FileNotFoundError, ValueError):
                continue
            if resolved_pdf.is_file() and not pdf_path.is_symlink():
                archive.write(resolved_pdf, f"my_reports/{file_stem}.pdf")

        for export in products:
            product = export["product"]
            product_id = _safe_name(str(product.get("product_id", "product")))
            product.pop("user_id", None)
            assets = export.pop("visual_assets", [])
            export["product"] = product
            asset_manifest = []
            for asset in assets:
                asset_id = _safe_name(str(asset.get("asset_id", "asset")))
                mime = asset.get("mime_type")
                extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg"}.get(mime)
                content = asset.pop("content", b"")
                asset.pop("user_id", None)
                if not extension or not isinstance(content, bytes):
                    continue
                relative = f"my_products/{product_id}/visuals/{asset_id}{extension}"
                archive.writestr(relative, content)
                asset["backup_path"] = f"visuals/{asset_id}{extension}"
                asset_manifest.append(asset)
            export["visual_assets"] = asset_manifest
            archive.writestr(
                f"my_products/{product_id}/product.json",
                json.dumps(export, ensure_ascii=False, indent=2, default=str) + "\n",
            )
    return buffer.getvalue()


__all__ = ["create_user_backup"]
