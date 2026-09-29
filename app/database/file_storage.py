"""Private/local binary object storage used by ProductStore visual assets."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import quote

import requests

from app.database.backend import StorageConfig


class StorageOperationError(RuntimeError):
    """A storage operation failed; callers must not silently switch backends."""


def _safe_key(key: str) -> str:
    path = PurePosixPath(str(key))
    if path.is_absolute() or not path.parts or any(p in {"", ".", ".."} for p in path.parts):
        raise ValueError("Invalid private asset key.")
    if "\\" in str(key) or "\x00" in str(key):
        raise ValueError("Invalid private asset key.")
    return path.as_posix()


class LocalFileStorage:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, content_type: str, *, overwrite: bool = True) -> str:
        relative = _safe_key(key)
        target = (self.root / relative).resolve()
        target.relative_to(self.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            if not overwrite and target.exists():
                raise FileExistsError("Private asset already exists.")
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return relative

    def get(self, key: str) -> bytes:
        relative = _safe_key(key)
        target = (self.root / relative).resolve()
        target.relative_to(self.root)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise StorageOperationError("Private local visual file could not be read.") from exc

    def delete(self, key: str) -> None:
        relative = _safe_key(key)
        target = (self.root / relative).resolve()
        target.relative_to(self.root)
        try:
            target.unlink(missing_ok=True)
            parent = target.parent
            while parent != self.root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        except OSError as exc:
            raise StorageOperationError("Private local visual file could not be removed.") from exc


class SupabasePrivateStorage:
    """Server-side access to a pre-created private bucket; emits no public/signed URLs."""
    def __init__(self, config: StorageConfig, timeout: int = 20):
        self.config = config
        self.timeout = timeout

    def _url(self, key: str, *, operation: str = "upload") -> str:
        relative = _safe_key(key)
        encoded = "/".join(quote(part, safe="") for part in relative.split("/"))
        root = f"{self.config.supabase_url}/storage/v1/object"
        bucket = quote(self.config.bucket, safe="")
        if operation == "delete":
            # Supabase Storage remove endpoint accepts {"prefixes": [object_key]}.
            return f"{root}/{bucket}"
        if operation == "download":
            # Private object retrieval: authenticated route, bytes are streamed server-side.
            return f"{root}/authenticated/{bucket}/{encoded}"
        return f"{root}/{bucket}/{encoded}"

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def put(self, key: str, content: bytes, content_type: str, *, overwrite: bool = False) -> str:
        relative = _safe_key(key)
        try:
            headers = self._headers(content_type)
            headers["x-upsert"] = "true" if overwrite else "false"
            response = requests.post(self._url(relative), data=content, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise StorageOperationError("Supabase private-file upload failed; no local fallback was attempted.") from exc
        return relative

    def get(self, key: str) -> bytes:
        try:
            response = requests.get(self._url(key, operation="download"), headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise StorageOperationError("Supabase private-file download failed; no local fallback was attempted.") from exc

    def delete(self, key: str) -> None:
        relative = _safe_key(key)
        try:
            response = requests.delete(
                self._url(relative, operation="delete"),
                json={"prefixes": [relative]},
                headers=self._headers("application/json"),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise StorageOperationError("Supabase private-file removal failed; no local fallback was attempted.") from exc
