import sqlite3
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from app.backup import create_user_backup
from app.database.db import ReportStore
from app.database.models import Report
from app.product.storage import ProductStore


with TemporaryDirectory() as temporary_directory:
    project = Path(temporary_directory)
    (project / "app").mkdir()
    (project / "data" / "reports").mkdir(parents=True)
    (project / "app" / "main.py").write_text("# application source\n")
    (project / "README.md").write_text("Project documentation\n")
    (project / "requirements.txt").write_text("pydantic\n")

    store = ReportStore(project / "data" / "opportunities.db")
    product_store = ProductStore(store.path)
    ok, message = store.register_user("alice", "correct-horse-123")
    assert ok, message
    ok, message = store.register_user("bob", "another-pass-456")
    assert ok, message
    alice = store.authenticate("alice", "correct-horse-123")
    bob = store.authenticate("bob", "another-pass-456")
    assert alice and bob
    alice_session = store.create_session(alice["user_id"])
    bob_session = store.create_session(bob["user_id"])

    store.save(Report(id="alice-report", topic="Alice private topic", executive_summary="ALICE_REPORT_PRIVATE_CONTENT", user_id=alice["user_id"]), alice["user_id"])
    store.save(Report(id="bob-report", topic="Bob private topic", executive_summary="BOB_REPORT_MUST_NOT_BE_EXPORTED", user_id=bob["user_id"]), bob["user_id"])
    for product_id, owner, title in (
        ("alice-product", alice["user_id"], "Alice product"),
        ("bob-product", bob["user_id"], "BOB_PRODUCT_MUST_NOT_BE_EXPORTED"),
    ):
        product_store.save(product_id=product_id, user_id=owner, source_report_id="report", opportunity_index=0,
                           opportunity_name=title, source_payload={}, inputs_payload={"title": title}, blueprint_payload=None)
    product_store.save_visual_asset(product_id="alice-product", user_id=alice["user_id"], asset_type="icon",
                                    title="Alice mark", filename="mark.svg", mime_type="image/svg+xml",
                                    placement="cover", metadata={}, content=b"<svg/>")

    report_dir = project / "data" / "reports"
    (report_dir / "opportunity-report-alice-report.pdf").write_bytes(b"ALICE_PRIVATE_PDF")
    (report_dir / "opportunity-report-bob-report.pdf").write_bytes(b"BOB_PRIVATE_PDF_MUST_NOT_BE_EXPORTED")
    with sqlite3.connect(store.path) as connection:
        account_ids = {row[0] for row in connection.execute("SELECT user_id FROM users")}
        password_hashes = {row[0] for row in connection.execute("SELECT password_hash FROM users")}
        session_hashes = {row[0] for row in connection.execute("SELECT token_hash FROM sessions")}

    try:
        create_user_backup(project, store, "")
    except ValueError:
        pass
    else:
        raise AssertionError("A backup must require a signed-in user ID")

    archive_bytes = create_user_backup(project, store, alice["user_id"])
    with ZipFile(BytesIO(archive_bytes)) as archive:
        names = set(archive.namelist())
        assert "app/main.py" in names
        assert "README.md" in names
        assert "requirements.txt" in names
        assert "USER_BACKUP_README.txt" in names
        assert "my_reports/alice-report.json" in names
        assert "my_reports/alice-report.pdf" in names
        assert "my_reports/bob-report.json" not in names
        assert "my_reports/bob-report.pdf" not in names
        assert "my_products/alice-product/product.json" in names
        assert "my_products/bob-product/product.json" not in names
        assert any(name.startswith("my_products/alice-product/visuals/") for name in names)
        assert not any("opportunities.db" in name for name in names)
        assert not any(name.startswith("data/") for name in names)

        alice_export = archive.read("my_reports/alice-report.json").decode("utf-8")
        assert "ALICE_REPORT_PRIVATE_CONTENT" in alice_export
        assert "user_id" not in alice_export
        assert "BOB_REPORT_MUST_NOT_BE_EXPORTED" not in alice_export
        product_export = archive.read("my_products/alice-product/product.json").decode("utf-8")
        assert "user_id" not in product_export
        assert archive.read("my_reports/alice-report.pdf") == b"ALICE_PRIVATE_PDF"
        all_exported_contents = b"".join(archive.read(name) for name in names)
        assert b"BOB_REPORT_MUST_NOT_BE_EXPORTED" not in all_exported_contents
        assert b"BOB_PRODUCT_MUST_NOT_BE_EXPORTED" not in all_exported_contents
        assert b"BOB_PRIVATE_PDF_MUST_NOT_BE_EXPORTED" not in all_exported_contents
        assert alice_session.encode() not in all_exported_contents
        assert bob_session.encode() not in all_exported_contents
        for secret_value in account_ids | password_hashes | session_hashes:
            assert secret_value.encode() not in all_exported_contents
        backup_readme = archive.read("USER_BACKUP_README.txt").decode("utf-8")
        assert "shared live database" in backup_readme
        assert "other users' data" in backup_readme

print("V2.4 owner-specific full backup isolation smoke test passed")
