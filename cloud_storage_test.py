import json
import os
import sqlite3
import ssl
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.database import backend
from app.database.backend import (
    StorageConfig, StorageConfigurationError, StorageConnectionError, _PostgresConnection, load_storage_config,
)
from app.database.db import ReportStore
from app.database.file_storage import LocalFileStorage, StorageOperationError, SupabasePrivateStorage
from app.product.storage import ProductStore


class FakeCursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakePostgres:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []
        self.last = None
        self.committed = False
        self.closed = False

    def cursor(self):
        self.last = FakeCursor(self.rows)
        self.executed.append(self.last)
        return self.last

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class StorageConfigTests(unittest.TestCase):
    def setUp(self):
        self.secrets = patch("app.database.backend._streamlit_secrets", return_value={})
        self.secrets.start()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.secrets.stop()

    def test_no_cloud_configuration_selects_local(self):
        self.assertEqual(load_storage_config().backend, "local")

    def test_partial_cloud_configuration_fails_closed(self):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        with self.assertRaisesRegex(StorageConfigurationError, "refusing local fallback"):
            load_storage_config()

    def test_bucket_only_configuration_is_not_treated_as_local(self):
        os.environ["SUPABASE_STORAGE_BUCKET"] = "private-bucket"
        with self.assertRaisesRegex(StorageConfigurationError, "refusing local fallback"):
            load_storage_config()

    def test_explicit_local_mode_is_a_deliberate_override(self):
        os.environ["APP_STORAGE_BACKEND"] = "local"
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        self.assertEqual(load_storage_config().backend, "local")

    def test_complete_but_malformed_cloud_config_fails(self):
        os.environ.update({
            "APP_STORAGE_BACKEND": "supabase",
            "SUPABASE_URL": "http://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "placeholder",
            "SUPABASE_DB_URL": "postgresql://user:pass@db.example.test/postgres?sslmode=require",
        })
        with self.assertRaisesRegex(StorageConfigurationError, "HTTPS"):
            load_storage_config()

    def test_complete_configuration_selects_supabase(self):
        os.environ.update({
            "APP_STORAGE_BACKEND": "supabase",
            "SUPABASE_URL": "https://example.supabase.co/",
            "SUPABASE_SERVICE_ROLE_KEY": "server-secret",
            "SUPABASE_DB_URL": "postgresql://user:pass@db.example.test/postgres?sslmode=require",
        })
        config = load_storage_config()
        self.assertEqual(config.backend, "supabase")
        self.assertEqual(config.bucket, "digital-product-assets")
        self.assertEqual(config.supabase_url, "https://example.supabase.co")
        self.assertNotIn("server-secret", repr(config))


class LocalFileTests(unittest.TestCase):
    def test_local_storage_roundtrip_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalFileStorage(Path(tmp) / "private")
            key = store.put("owner/product/picture.png", b"private image", "image/png")
            self.assertEqual(store.get(key), b"private image")
            self.assertTrue((Path(tmp) / "private" / key).is_file())
            with self.assertRaises(ValueError):
                store.get("../../outside")
            store.delete(key)
            with self.assertRaises(StorageOperationError):
                store.get(key)

    def test_product_visual_bytes_are_file_backed_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "app.db"
            store = ProductStore(db)
            store.save(product_id="p1", user_id="u1", source_report_id="r1", opportunity_index=0,
                       opportunity_name="Sample", source_payload={}, inputs_payload={}, blueprint_payload=None)
            saved = store.save_visual_asset(product_id="p1", user_id="u1", asset_type="icon", title="Mark",
                                             filename="mark.svg", mime_type="image/svg+xml", placement="cover",
                                             metadata={}, content=b"<svg></svg>")
            self.assertEqual(store.get_visual_assets("p1", "u1")[0]["content"], b"<svg></svg>")
            self.assertEqual(store.get_visual_assets("p1", "u2"), [])
            with sqlite3.connect(db) as conn:
                content, key = conn.execute("SELECT content,storage_path FROM product_assets WHERE asset_id=?", (saved["asset_id"],)).fetchone()
            self.assertEqual(bytes(content), b"")
            self.assertTrue(key)
            self.assertTrue((Path(tmp) / "visual_assets" / key).is_file())


class SupabaseRestTests(unittest.TestCase):
    def setUp(self):
        self.config = StorageConfig(
            backend="supabase", supabase_url="https://sample.supabase.co",
            service_role_key="never-print-this", database_url="postgresql://u:p@host/db",
        )
        self.storage = SupabasePrivateStorage(self.config)

    @patch("app.database.file_storage.requests.post")
    @patch("app.database.file_storage.requests.get")
    @patch("app.database.file_storage.requests.delete")
    def test_upload_private_download_and_remove_use_authenticated_routes(self, delete, get, post):
        for operation in (post, get, delete):
            operation.return_value = Mock(status_code=200, content=b"image", raise_for_status=Mock())
        key = self.storage.put("u1/p1/asset.png", b"image", "image/png")
        self.assertEqual(key, "u1/p1/asset.png")
        self.assertEqual(post.call_args.args[0], "https://sample.supabase.co/storage/v1/object/digital-product-assets/u1/p1/asset.png")
        self.assertEqual(post.call_args.kwargs["headers"]["x-upsert"], "false")
        self.assertEqual(self.storage.get(key), b"image")
        self.assertEqual(get.call_args.args[0], "https://sample.supabase.co/storage/v1/object/authenticated/digital-product-assets/u1/p1/asset.png")
        self.storage.delete(key)
        self.assertEqual(delete.call_args.args[0], "https://sample.supabase.co/storage/v1/object/digital-product-assets")
        self.assertEqual(delete.call_args.kwargs["json"], {"prefixes": [key]})
        self.assertNotIn("/public/", post.call_args.args[0])
        self.assertNotIn("signedURL", repr(get.call_args))

    @patch("app.database.file_storage.requests.post", side_effect=requests.ConnectionError("offline"))
    def test_cloud_failures_raise_instead_of_falling_back(self, _post):
        with self.assertRaisesRegex(StorageOperationError, "no local fallback"):
            self.storage.put("u/p/a.png", b"x", "image/png")

    def test_postgres_adapter_translates_placeholders_and_pragmas_without_live_db(self):
        fake = FakePostgres()
        with _PostgresConnection(fake) as conn:
            conn.execute("SELECT * FROM reports WHERE id=? AND user_id=?", ("r", "u"))
        self.assertEqual(fake.last.sql, "SELECT * FROM reports WHERE id=%s AND user_id=%s")
        self.assertEqual(fake.last.params, ("r", "u"))
        self.assertTrue(fake.committed)
        self.assertTrue(fake.closed)

    @patch("app.database.backend.sqlite3.connect")
    @patch("pg8000.dbapi.connect", side_effect=OSError("unavailable"))
    @patch("app.database.backend.load_storage_config")
    def test_cloud_database_connection_failure_never_opens_sqlite(self, config, _pg, local_sqlite):
        config.return_value = self.config
        with self.assertRaisesRegex(RuntimeError, "local fallback is disabled"):
            backend.connect_database("postgresql://unused", cloud=True)
        local_sqlite.assert_not_called()


class FakeReport:
    """Minimal stand-in for the Report model used by ReportStore.save."""
    def __init__(self, report_id, owner, topic="topic"):
        self.id, self.user_id, self.topic = report_id, owner, topic
        self.created_at = datetime.now(timezone.utc)

    def model_dump_json(self):
        return json.dumps({"id": self.id, "topic": self.topic, "user_id": self.user_id})


class PostgresAdapterRegressionTests(unittest.TestCase):
    CLOUD = StorageConfig(
        backend="supabase", supabase_url="https://sample.supabase.co",
        service_role_key="never-print-this", database_url="postgresql://u:p@host/db",
    )

    def setUp(self):
        backend._schema_done.clear()

    def tearDown(self):
        backend._schema_done.clear()

    def test_pragma_result_can_be_iterated_directly(self):
        fake = FakePostgres(rows=[(1, "product_id"), (2, "user_id")])
        with _PostgresConnection(fake) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        self.assertEqual(columns, {"product_id", "user_id"})

    def test_product_store_cloud_setup_works_through_adapter_and_runs_once(self):
        existing_columns = [(1, "content_payload"), (2, "design_template_id"), (3, "page_size"), (4, "storage_path")]
        connections = []

        def connect(path, cloud=False):
            fake = FakePostgres(rows=existing_columns)
            connections.append(fake)
            return _PostgresConnection(fake)

        with patch("app.product.storage.load_storage_config", return_value=self.CLOUD), \
                patch("app.product.storage.connect_database", side_effect=connect):
            ProductStore()
            ProductStore()
        self.assertEqual(len(connections), 1, "schema/RLS setup must run once per process, not per instance")
        rls = [c.sql for c in connections[0].executed if "ROW LEVEL SECURITY" in getattr(c, "sql", "")]
        self.assertEqual(len(rls), 5)
        self.assertFalse([sql for sql in rls if "public.users" in sql or "public.reports" in sql],
                         "ProductStore must not depend on tables owned by ReportStore")

    def test_schema_guard_retries_after_a_failed_setup(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("temporary")

        with self.assertRaises(RuntimeError):
            backend.run_once_per_process("guard-test", flaky)
        backend.run_once_per_process("guard-test", flaky)
        backend.run_once_per_process("guard-test", flaky)
        self.assertEqual(len(calls), 2)


class TlsModeTests(unittest.TestCase):
    def test_default_and_require_encrypt_without_certificate_verification(self):
        for query in ("", "sslmode=require"):
            context = backend._ssl_context(query)
            self.assertEqual(context.verify_mode, ssl.CERT_NONE)
            self.assertFalse(context.check_hostname)

    def test_verify_modes_check_the_certificate_chain(self):
        full = backend._ssl_context("sslmode=verify-full")
        self.assertEqual(full.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(full.check_hostname)
        chain_only = backend._ssl_context("sslmode=verify-ca")
        self.assertEqual(chain_only.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(chain_only.check_hostname)

    def test_modes_that_can_downgrade_to_plaintext_are_rejected(self):
        for mode in ("prefer", "allow", "disable", "bogus"):
            with self.assertRaises(StorageConfigurationError):
                backend._ssl_context(f"sslmode={mode}")

    def test_missing_root_certificate_file_is_a_configuration_error(self):
        with self.assertRaises(StorageConfigurationError):
            backend._ssl_context("sslmode=verify-full&sslrootcert=/nonexistent/ca.crt")

    @patch("app.database.backend._streamlit_secrets", return_value={})
    def test_load_config_rejects_prefer_sslmode(self, _secrets):
        env = {
            "APP_STORAGE_BACKEND": "supabase", "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "server-secret",
            "SUPABASE_DB_URL": "postgresql://user:pass@db.example.test/postgres?sslmode=prefer",
        }
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(StorageConfigurationError, "sslmode"):
            load_storage_config()

    @patch("pg8000.dbapi.connect", side_effect=ssl.SSLCertVerificationError("self-signed certificate in certificate chain"))
    @patch("app.database.backend.load_storage_config")
    def test_tls_failure_reports_class_and_hint_but_no_secrets(self, config, _pg):
        config.return_value = StorageConfig(
            backend="supabase", supabase_url="https://sample.supabase.co", service_role_key="never-print-this",
            database_url="postgresql://user:secretpw@host.example/db?sslmode=verify-full",
        )
        with self.assertRaises(StorageConnectionError) as caught:
            backend.connect_database("unused", cloud=True)
        message = str(caught.exception)
        self.assertIn("SSLCertVerificationError", message)
        self.assertIn("sslrootcert", message)
        self.assertIn("local fallback is disabled", message)
        for secret in ("secretpw", "never-print-this", "host.example"):
            self.assertNotIn(secret, message)


class ReportOwnershipTests(unittest.TestCase):
    def test_upsert_cannot_take_over_another_accounts_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "app.db"
            store = ReportStore(db)
            store.save(FakeReport("r1", "alice", "Alice v1"), "alice")
            with self.assertRaises(PermissionError):
                store.save(FakeReport("r1", "bob", "Bob overwrite"), "bob")
            store.save(FakeReport("r1", "alice", "Alice v2"), "alice")  # the owner may still update
            with sqlite3.connect(db) as conn:
                owner, payload = conn.execute("SELECT user_id, payload FROM reports WHERE id='r1'").fetchone()
            self.assertEqual(owner, "alice")
            self.assertIn("Alice v2", payload)
            self.assertNotIn("Bob", payload)


class VisualCleanupTests(unittest.TestCase):
    def _store(self, tmp):
        store = ProductStore(Path(tmp) / "app.db")
        store.save(product_id="p1", user_id="u1", source_report_id="r1", opportunity_index=0,
                   opportunity_name="Sample", source_payload={}, inputs_payload={}, blueprint_payload=None)
        return store

    def test_delete_still_succeeds_when_object_removal_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            saved = store.save_visual_asset(product_id="p1", user_id="u1", asset_type="icon", title="Mark",
                                            filename="mark.svg", mime_type="image/svg+xml", placement="cover",
                                            metadata={}, content=b"<svg></svg>")
            with patch.object(store.visual_storage, "delete", side_effect=StorageOperationError("down")) as delete:
                self.assertTrue(store.delete_visual_asset(product_id="p1", user_id="u1", asset_id=saved["asset_id"]))
            self.assertEqual(delete.call_count, 2)  # one retry, then logged as orphaned
            self.assertEqual(store.list_visual_assets("p1", "u1"), [])

    def test_failed_save_cleanup_does_not_mask_the_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            with patch.object(store.visual_storage, "delete", side_effect=StorageOperationError("down")):
                with self.assertRaises(LookupError):
                    store.save_visual_asset(product_id="missing", user_id="u1", asset_type="icon", title="Mark",
                                            filename="mark.svg", mime_type="image/svg+xml", placement="cover",
                                            metadata={}, content=b"<svg></svg>")


if __name__ == "__main__":
    unittest.main()
