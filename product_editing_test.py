import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.product.editing import (
    replace_content_section,
    replace_content_section_edits,
    replace_product_cover,
)
from app.product.product_schema import ContentBlock, GeneratedSection, ProductContent
from app.product.storage import ProductStore
from app.ui.product_generator import _table_editor_rows


FINGERPRINT = "a" * 64


def sample_content() -> ProductContent:
    return ProductContent(
        product_title="Weekly Planning Workbook",
        subtitle="A practical planning concept",
        blueprint_fingerprint=FINGERPRINT,
        sections=[
            GeneratedSection(
                section_index=0,
                title="Start Here",
                purpose="Set a useful goal.",
                blocks=[
                    ContentBlock(kind="paragraph", title="A first step", body="Choose one practical outcome."),
                    ContentBlock(kind="example", title="Illustrative example", body="A hypothetical example for a busy week."),
                ],
            ),
            GeneratedSection(
                section_index=1,
                title="Weekly Workflow",
                purpose="Set up the planning sequence.",
                blocks=[ContentBlock(kind="steps", items=["Choose a time", "Review the plan"])],
            ),
        ],
    )


class ProductEditingTests(unittest.TestCase):
    def test_table_editor_requires_rows_to_match_user_selected_columns(self):
        self.assertEqual(_table_editor_rows("Monday\tSoup\nTuesday\tSalad", 2), [["Monday", "Soup"], ["Tuesday", "Salad"]])
        with self.assertRaisesRegex(ValueError, "cells separated by tabs"):
            _table_editor_rows("Monday", 2)
        with self.assertRaisesRegex(ValueError, "20 rows"):
            _table_editor_rows("\n".join("a\tb" for _ in range(21)), 2)

    def test_section_edits_change_only_selected_content(self):
        content = sample_content()
        original_second = content.sections[1].model_dump(mode="json")
        updated = replace_content_section_edits(
            content,
            section_index=0,
            title="A Small Start",
            purpose="Choose a first action you can repeat.",
            blocks=[
                {"kind": "paragraph", "title": "Try this", "body": "Pick one action."},
                {"kind": "example", "title": "Example", "body": "An edited illustrative example."},
            ],
        )
        self.assertEqual(updated.sections[0].title, "A Small Start")
        self.assertEqual(updated.sections[0].blocks[1].body, "An edited illustrative example.")
        self.assertEqual(updated.sections[1].model_dump(mode="json"), original_second)
        self.assertEqual(updated.blueprint_fingerprint, content.blueprint_fingerprint)
        self.assertEqual(updated.product_title, content.product_title)
        self.assertEqual(updated.subtitle, content.subtitle)

    def test_section_regeneration_replaces_one_section_and_preserves_cover_and_neighbors(self):
        content = sample_content()
        original_second = content.sections[1].model_dump(mode="json")
        regenerated = GeneratedSection(
            section_index=0,
            title="Start Here",
            purpose="Choose a practical goal.",
            blocks=[ContentBlock(kind="steps", items=["Name one goal", "Choose a time"])],
        )
        updated = replace_content_section(content, regenerated)
        self.assertEqual(updated.sections[0].blocks[0].items, ["Name one goal", "Choose a time"])
        self.assertEqual(updated.sections[1].model_dump(mode="json"), original_second)
        self.assertEqual(updated.product_title, content.product_title)
        self.assertEqual(updated.subtitle, content.subtitle)
        self.assertEqual(updated.blueprint_fingerprint, content.blueprint_fingerprint)

    def test_cover_copy_is_validated_and_legacy_content_defaults_subtitle(self):
        content = ProductContent.model_validate({
            "product_title": "Legacy title",
            "blueprint_fingerprint": FINGERPRINT,
            "sections": [],
        })
        self.assertEqual(content.subtitle, "")
        updated = replace_product_cover(content, title="New title", subtitle="New subtitle")
        self.assertEqual((updated.product_title, updated.subtitle), ("New title", "New subtitle"))
        with self.assertRaises(ValueError):
            replace_product_cover(content, title="", subtitle="Subtitle")

    def test_edit_and_regeneration_snapshots_are_versioned_and_account_scoped(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "products.db")
            blueprint = {"title": "Approved concept"}
            fingerprint = hashlib.sha256(
                json.dumps(blueprint, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            store.save(
                product_id="p1", user_id="owner-a", source_report_id="r1", opportunity_index=0,
                opportunity_name="Planning workbook", source_payload={"report_id": "r1"},
                inputs_payload={"product_title": "Planning workbook"}, blueprint_payload=blueprint,
                status="approved",
            )
            content = sample_content().model_copy(update={"blueprint_fingerprint": fingerprint})
            saved = store.save_content(
                product_id="p1", user_id="owner-a", content_payload=content.model_dump(mode="json"),
                change_summary="Generated initial content",
            )
            edited = replace_product_cover(content, title="Owner's revised title", subtitle="Owner's subtitle")
            edited = replace_content_section_edits(
                edited, section_index=0, title="Edited section", purpose="Edited purpose.",
                blocks=[{"kind": "paragraph", "title": "Text", "body": "Edited text."}],
            )
            saved = store.save_content(
                product_id="p1", user_id="owner-a", content_payload=edited.model_dump(mode="json"),
                change_summary="Edited cover and section 1",
            )
            self.assertEqual(saved["content_payload"]["product_title"], "Owner's revised title")
            self.assertEqual([item["version_number"] for item in saved["content_versions"]], [1, 2])
            self.assertEqual(saved["content_versions"][-1]["change_summary"], "Edited cover and section 1")
            self.assertIsNone(store.get("p1", "owner-b"))
            with self.assertRaises(PermissionError):
                store.save_content(
                    product_id="p1", user_id="owner-b", content_payload=edited.model_dump(mode="json"),
                    change_summary="Unauthorized update",
                )


if __name__ == "__main__":
    unittest.main()
