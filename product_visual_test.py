import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from PIL import Image

from app.product.product_schema import ContentBlock, GeneratedSection, ProductContent
from app.product.storage import ProductStore
from app.product.template_engine import render_product_html
from app.product.visual_generator import (
    MAX_UPLOAD_BYTES,
    VisualGenerationError,
    create_visual_asset,
    sanitize_uploaded_image,
)


def sample_content() -> ProductContent:
    return ProductContent(
        product_title="Phase 5 Visual Test",
        blueprint_fingerprint="0123456789abcdef0123456789abcdef",
        sections=[GeneratedSection(
            section_index=0,
            title="Start Here",
            purpose="Set up a repeatable process.",
            blocks=[ContentBlock(kind="paragraph", body="Begin with one small step.")],
        )],
    )


def seed_product(store: ProductStore, product_id: str = "p1", user_id: str = "owner-a") -> None:
    store.save(
        product_id=product_id,
        user_id=user_id,
        source_report_id="r1",
        opportunity_index=0,
        opportunity_name="Test opportunity",
        source_payload={"report_id": "r1"},
        inputs_payload={"product_title": "Test product"},
        blueprint_payload=None,
    )


class ProductVisualTests(unittest.TestCase):
    def test_deterministic_visuals_are_valid_escaped_svg(self):
        created = [
            create_visual_asset("icon", "<Plan & Check>"),
            create_visual_asset("shape", "Weekly milestone", shape="section_marker"),
            create_visual_asset("diagram", "Four-step routine", steps=["Choose a goal", "Plan", "Try", "Review"]),
        ]
        for asset in created:
            self.assertEqual(asset.mime_type, "image/svg+xml")
            root = ET.fromstring(asset.content)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertIsNone(root.find(".//{http://www.w3.org/2000/svg}script"))
        self.assertIn("&lt;Plan &amp; Check&gt;", created[0].content.decode())
        self.assertEqual(created[2].metadata["generator"], "deterministic_svg")

    def test_visual_input_validation_returns_actionable_errors(self):
        with self.assertRaisesRegex(VisualGenerationError, "short title"):
            create_visual_asset("icon", "  ")
        with self.assertRaisesRegex(VisualGenerationError, "2 and 5 steps"):
            create_visual_asset("diagram", "Flow", steps=["Only one"])
        with self.assertRaisesRegex(VisualGenerationError, "60 characters"):
            create_visual_asset("diagram", "Flow", steps=["x" * 61, "Next"])
        with self.assertRaisesRegex(VisualGenerationError, "accent colors"):
            create_visual_asset("shape", "Milestone", accent_color="#000000")

    def test_upload_validation_reencodes_png_and_jpeg_and_rejects_invalid_inputs(self):
        for image_format, extension, expected_mime in [
            ("PNG", "misleading.jpg", "image/png"),
            ("JPEG", "photo.png", "image/jpeg"),
        ]:
            source = BytesIO()
            Image.new("RGB", (16, 12), (20, 80, 140)).save(source, format=image_format)
            sanitized = sanitize_uploaded_image(source.getvalue(), extension)
            self.assertEqual(sanitized.asset_type, "upload")
            self.assertEqual(sanitized.mime_type, expected_mime)
            self.assertTrue(sanitized.metadata["reencoded"])
            with Image.open(BytesIO(sanitized.content)) as decoded:
                self.assertEqual(decoded.format, image_format)
                self.assertEqual(decoded.size, (16, 12))
        with self.assertRaisesRegex(VisualGenerationError, "exporting it as PNG or JPEG"):
            sanitize_uploaded_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image.svg")
        with self.assertRaisesRegex(VisualGenerationError, "2 MB or smaller"):
            sanitize_uploaded_image(b"x" * (MAX_UPLOAD_BYTES + 1), "large.png")
        with self.assertRaisesRegex(VisualGenerationError, "not a valid supported image"):
            sanitize_uploaded_image(b"not an image", "broken.png")

    def test_visual_storage_is_persistent_and_scoped_to_owner_and_product(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assets.db"
            store = ProductStore(path)
            seed_product(store)
            asset = create_visual_asset("diagram", "Meal routine", steps=["Plan meals", "Shop", "Prepare"])
            saved = store.save_visual_asset(
                product_id="p1", user_id="owner-a", asset_type=asset.asset_type, title=asset.title,
                filename=asset.filename, mime_type=asset.mime_type, placement="section:0",
                metadata=asset.metadata, content=asset.content,
            )
            self.assertTrue(saved["asset_id"])
            self.assertEqual(store.list_visual_assets("p1", "owner-a")[0]["title"], "Meal routine")
            self.assertEqual(store.get_visual_assets("p1", "owner-a")[0]["content"], asset.content)
            self.assertEqual(store.list_visual_assets("p1", "owner-b"), [])
            self.assertEqual(store.get_visual_assets("p1", "owner-b"), [])
            self.assertFalse(store.delete_visual_asset(product_id="p1", user_id="owner-b", asset_id=saved["asset_id"]))
            with self.assertRaises(PermissionError):
                store.save_visual_asset(
                    product_id="p1", user_id="owner-b", asset_type=asset.asset_type, title=asset.title,
                    filename=asset.filename, mime_type=asset.mime_type, placement="cover",
                    metadata=asset.metadata, content=asset.content,
                )
            with self.assertRaisesRegex(ValueError, "cover or one"):
                store.save_visual_asset(
                    product_id="p1", user_id="owner-a", asset_type=asset.asset_type, title=asset.title,
                    filename=asset.filename, mime_type=asset.mime_type, placement="section:99",
                    metadata=asset.metadata, content=asset.content,
                )
            reopened = ProductStore(path)
            self.assertEqual(len(reopened.get_visual_assets("p1", "owner-a")), 1)
            self.assertTrue(reopened.delete_visual_asset(product_id="p1", user_id="owner-a", asset_id=saved["asset_id"]))
            self.assertEqual(reopened.list_visual_assets("p1", "owner-a"), [])

    def test_visual_asset_limit_is_enforced_per_product_owner(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "limit.db")
            seed_product(store)
            for index in range(12):
                asset = create_visual_asset("icon", f"Icon {index}")
                store.save_visual_asset(
                    product_id="p1", user_id="owner-a", asset_type=asset.asset_type, title=asset.title,
                    filename=asset.filename, mime_type=asset.mime_type, placement="cover",
                    metadata=asset.metadata, content=asset.content,
                )
            asset = create_visual_asset("icon", "Thirteenth")
            with self.assertRaisesRegex(ValueError, "maximum of 12"):
                store.save_visual_asset(
                    product_id="p1", user_id="owner-a", asset_type=asset.asset_type, title=asset.title,
                    filename=asset.filename, mime_type=asset.mime_type, placement="cover",
                    metadata=asset.metadata, content=asset.content,
                )

    def test_design_renderer_places_only_supported_assets_on_cover_or_sections(self):
        content = sample_content()
        cover = create_visual_asset("icon", "Cover visual")
        section = create_visual_asset("shape", "Section visual")
        html = render_product_html(
            content,
            "minimal_professional",
            visual_assets=[
                {"mime_type": cover.mime_type, "content": cover.content, "title": cover.title, "placement": "cover"},
                {"mime_type": section.mime_type, "content": section.content, "title": section.title, "placement": "section:0"},
                {"mime_type": "text/html", "content": b"<script>bad()</script>", "title": "ignored", "placement": "cover"},
                {"mime_type": "image/png", "content": b"x" * (MAX_UPLOAD_BYTES + 1), "title": "oversized", "placement": "cover"},
            ],
        )
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertIn("Cover visual</figcaption>", html)
        self.assertIn("Section visual</figcaption>", html)
        self.assertNotIn("text/html", html)
        self.assertNotIn("oversized</figcaption>", html)
        self.assertLess(html.index("Cover visual</figcaption>"), html.index("SECTION 01"))
        self.assertLess(html.index("Section visual</figcaption>"), html.index("Begin with one small step."))

    def test_visual_table_migration_does_not_change_existing_product_columns(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            store = ProductStore(path)
            seed_product(store)
            with sqlite3.connect(path) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
            self.assertIn("product_assets", tables)
            self.assertIn("content_payload", columns)
            self.assertIn("design_template_id", columns)
            self.assertIsNotNone(store.get("p1", "owner-a"))


if __name__ == "__main__":
    unittest.main()
