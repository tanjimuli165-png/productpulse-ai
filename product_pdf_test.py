import hashlib
import json
import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

from app.product.pdf_builder import ProductPdfExportError, export_saved_product_pdf
from app.product.product_schema import (
    BlueprintSection,
    ContentBlock,
    EvidenceReference,
    GeneratedSection,
    ProductBlueprint,
    ProductContent,
    ProductDesign,
    ProductInputs,
)
from app.product.qa import qa_snapshot_fingerprint
from app.product.storage import ProductStore
from app.product.template_engine import render_product_html
from app.product.visual_generator import create_visual_asset


EVIDENCE = EvidenceReference(
    evidence_id="EV-123456789ABC",
    source_title="Freelancer planning thread",
    source_type="forum",
    customer_language="I struggle to plan focused client work every week.",
    url="https://example.org/source?a=1&b=2",
)


def blueprint() -> ProductBlueprint:
    return ProductBlueprint(
        title="Weekly Client Work Workbook",
        subtitle="A practical planning workbook",
        target_audience="Freelancers managing client work",
        core_problem="Freelancers struggle to plan focused client work every week.",
        desired_outcome="Create a realistic weekly client-work plan.",
        promise="Plan focused client work with a repeatable weekly process.",
        product_type="Workbook",
        recommended_types=["Workbook"],
        recommendation_reason="A workbook supports implementation.",
        outline=[
            BlueprintSection(title="Plan the Week", purpose="Create a focused weekly plan.", components=["steps", "exercise", "table"]),
            BlueprintSection(title="Review Progress", purpose="Review progress and adjust.", components=["checklist"]),
            BlueprintSection(title="Prepare Next Week", purpose="Choose the next action.", components=["action_steps"]),
        ],
        estimated_page_count=8,
        exercises=["Choose a weekly priority"],
        checklists=["Weekly review checklist"],
        worksheets=["Client work planner"],
        examples=[],
        templates=[],
        bonuses=[],
        design_direction="Clear workbook pages with writing space.",
    )


def product_content(blueprint_value: ProductBlueprint | None = None) -> ProductContent:
    bp = blueprint_value or blueprint()
    fingerprint = hashlib.sha256(
        json.dumps(bp.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return ProductContent(
        product_title="Weekly Client Work Workbook",
        subtitle="Plan focused client work without losing sight of client commitments.",
        blueprint_fingerprint=fingerprint,
        sections=[
            GeneratedSection(
                section_index=0,
                title="Plan the Week",
                purpose="Create a focused client-work plan for this week.",
                blocks=[
                    ContentBlock(kind="steps", title="Start with one outcome", items=["List active client commitments", "Choose one weekly priority", "Block a focus period"]),
                    ContentBlock(kind="exercise", title="Weekly planning exercise", body="Choose a realistic client-work outcome and record the next focused action."),
                    ContentBlock(kind="table", title="Weekly planner", columns=["Day", "Client task"], rows=[["Monday", "Draft proposal"], ["Tuesday", "Review client notes"]]),
                    ContentBlock(kind="reference", title="Research note", body="Source linked to the opportunity hypothesis.", evidence_ids=[EVIDENCE.evidence_id]),
                ],
            ),
            GeneratedSection(
                section_index=1,
                title="Review Progress",
                purpose="Review progress and adjust next week's plan.",
                blocks=[ContentBlock(kind="checklist", title="Weekly review", items=["Review completed client tasks", "Select one adjustment for next week"])],
            ),
            GeneratedSection(
                section_index=2,
                title="Prepare Next Week",
                purpose="Choose a next action for the coming week.",
                blocks=[ContentBlock(kind="action_steps", items=["Choose one next-week priority", "Schedule a focused work period"])],
            ),
        ],
    )


def inputs() -> ProductInputs:
    return ProductInputs(
        product_title="Weekly Client Work Workbook",
        audience="Freelancers managing client work",
        problem="Freelancers struggle to plan focused client work every week.",
        promise="Plan focused client work with a repeatable weekly process.",
        evidence=[EVIDENCE],
    )


def add_product(store: ProductStore, *, product_id: str = "private-product", user_id: str = "owner-a", status: str = "approved") -> tuple[ProductBlueprint, ProductContent, ProductInputs]:
    bp = blueprint()
    product_inputs = inputs()
    content = product_content(bp)
    store.save(
        product_id=product_id,
        user_id=user_id,
        source_report_id="report-1",
        opportunity_index=0,
        opportunity_name="Weekly Client Work Workbook",
        source_payload={"report_id": "report-1", "opportunity_name": "Weekly Client Work Workbook"},
        inputs_payload=product_inputs.model_dump(mode="json"),
        blueprint_payload=bp.model_dump(mode="json"),
        status=status,
    )
    store.save_design(product_id=product_id, user_id=user_id, design=ProductDesign(template_id="modern_business", page_size="letter"))
    if status == "approved":
        store.save_content(
            product_id=product_id,
            user_id=user_id,
            content_payload=content.model_dump(mode="json"),
            change_summary="Initial saved product content",
        )
    return bp, content, product_inputs


def tiny_png() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (40, 30), color=(45, 91, 186))
    image.save(output, format="PNG")
    return output.getvalue()


def save_pass_qa(store, product_id, user_id, blueprint_value, content_value, inputs_value, *, template_id, page_size, assets):
    preview = render_product_html(
        content_value,
        template_id,
        page_size,
        subtitle=content_value.subtitle,
        product_type=blueprint_value.product_type,
        audience=blueprint_value.target_audience,
        evidence=inputs_value.evidence,
        visual_assets=assets,
    )
    fingerprint = qa_snapshot_fingerprint(
        blueprint_value,
        content_value,
        inputs=inputs_value,
        design={"template_id": template_id, "page_size": page_size},
        visual_assets=assets,
        preview_html=preview,
    )
    store.save_qa_run(
        product_id=product_id,
        user_id=user_id,
        snapshot_fingerprint=fingerprint,
        result_payload={"status": "PASS", "checks": [{"name": "Fixture QA", "status": "PASS"}]},
    )


class ProductPdfExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.store = ProductStore(Path(self.directory.name) / "products.db")
        self.bp, self.content, self.product_inputs = add_product(self.store)

    def tearDown(self):
        self.directory.cleanup()

    def test_export_builds_a_readable_product_pdf_using_saved_design_content_and_visuals(self):
        vector = create_visual_asset("diagram", "Weekly planning flow", steps=["Review tasks", "Choose focus", "Plan time"])
        self.store.save_visual_asset(
            product_id="private-product", user_id="owner-a", asset_type=vector.asset_type,
            title=vector.title, filename=vector.filename, mime_type=vector.mime_type,
            placement="cover", metadata=vector.metadata, content=vector.content,
        )
        png = tiny_png()
        self.store.save_visual_asset(
            product_id="private-product", user_id="owner-a", asset_type="upload",
            title="Weekly workbook image", filename="weekly.png", mime_type="image/png",
            placement="section:0", metadata={"generator": "validated_upload"}, content=png,
        )
        save_pass_qa(
            self.store, "private-product", "owner-a", self.bp, self.content, self.product_inputs,
            template_id="modern_business", page_size="letter",
            assets=self.store.get_visual_assets("private-product", "owner-a"),
        )

        result = export_saved_product_pdf("private-product", "owner-a", self.store)
        reader = PdfReader(BytesIO(result.pdf_bytes), strict=True)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

        self.assertTrue(result.pdf_bytes.startswith(b"%PDF-"))
        self.assertEqual(result.filename, "weekly-client-work-workbook.pdf")
        self.assertEqual(result.template_name, "Modern Business")
        self.assertEqual(result.page_size_label, "US Letter")
        self.assertEqual(result.included_visual_count, 2)
        self.assertGreaterEqual(result.page_count, 3)  # cover, contents, and compacted section content
        self.assertEqual(result.page_count, len(reader.pages))
        self.assertIn("Weekly Client Work Workbook", extracted)
        self.assertIn("Contents", extracted)
        self.assertIn("Plan the Week", extracted)
        self.assertIn("Review Progress", extracted)
        self.assertIn("Monday", extracted)
        self.assertIn("EV-123456789ABC", extracted)
        self.assertIn("Designed for:", extracted)
        self.assertIn("Outcome:", extracted)
        self.assertNotIn("Opportunity hypothesis", extracted)
        self.assertTrue(any(len(page.images) for page in reader.pages))
        self.assertEqual(result.latest_qa_status, "PASS")
        self.assertTrue(any("human review" in item.lower() for item in result.limitations))
        self.assertFalse(Path(self.directory.name, result.filename).exists())

    def test_repeated_export_produces_byte_identical_pdf_for_same_saved_snapshot(self):
        self.store.save_design(
            product_id="private-product",
            user_id="owner-a",
            design={"template_id": "minimal_professional", "page_size": "letter"},
        )
        save_pass_qa(
            self.store,
            "private-product",
            "owner-a",
            self.bp,
            self.content,
            self.product_inputs,
            template_id="minimal_professional",
            page_size="letter",
            assets=[],
        )

        first = export_saved_product_pdf("private-product", "owner-a", self.store)
        second = export_saved_product_pdf("private-product", "owner-a", self.store)

        self.assertEqual(first.pdf_bytes, second.pdf_bytes)
        self.assertEqual(
            hashlib.sha256(first.pdf_bytes).hexdigest(),
            hashlib.sha256(second.pdf_bytes).hexdigest(),
        )


    def test_saved_a4_page_size_is_used_and_matching_qa_status_is_reported(self):
        self.store.save_design(product_id="private-product", user_id="owner-a", design={"template_id": "clean_workbook", "page_size": "a4"})
        assets = self.store.get_visual_assets("private-product", "owner-a")
        preview = render_product_html(
            self.content, "clean_workbook", "a4", subtitle=self.content.subtitle,
            product_type=self.bp.product_type, audience=self.bp.target_audience,
            evidence=self.product_inputs.evidence, visual_assets=assets,
        )
        fingerprint = qa_snapshot_fingerprint(
            self.bp, self.content, inputs=self.product_inputs,
            design={"template_id": "clean_workbook", "page_size": "a4"},
            visual_assets=assets, preview_html=preview,
        )
        self.store.save_qa_run(
            product_id="private-product", user_id="owner-a", snapshot_fingerprint=fingerprint,
            result_payload={"status": "PASS", "checks": [{"name": "Example", "status": "PASS"}]},
        )

        result = export_saved_product_pdf("private-product", "owner-a", self.store)
        reader = PdfReader(BytesIO(result.pdf_bytes), strict=True)
        width = float(reader.pages[0].mediabox.width)
        height = float(reader.pages[0].mediabox.height)

        self.assertAlmostEqual(width, 595.28, places=1)
        self.assertAlmostEqual(height, 841.89, places=1)
        self.assertEqual(result.template_name, "Clean Workbook")
        self.assertEqual(result.page_size_label, "A4")
        self.assertEqual(result.latest_qa_status, "PASS")
        self.assertTrue(result.latest_qa_is_current)

        self.store.save_design(product_id="private-product", user_id="owner-a", design={"template_id": "clean_workbook", "page_size": "letter"})
        with self.assertRaisesRegex(ProductPdfExportError, "stale"):
            export_saved_product_pdf("private-product", "owner-a", self.store)

    def test_export_is_owner_scoped_and_requires_approval_and_complete_saved_content(self):
        self.assertIsNone(self.store.get("private-product", "owner-b"))
        with self.assertRaises(ProductPdfExportError):
            export_saved_product_pdf("private-product", "owner-b", self.store)
        with self.assertRaises(ProductPdfExportError):
            export_saved_product_pdf("private-product", "", self.store)

        self.store.save(
            product_id="draft-product", user_id="owner-a", source_report_id="report-1", opportunity_index=0,
            opportunity_name="Draft", source_payload={}, inputs_payload=self.product_inputs.model_dump(mode="json"),
            blueprint_payload=self.bp.model_dump(mode="json"), status="draft",
        )
        with self.assertRaisesRegex(ProductPdfExportError, "Approve"):
            export_saved_product_pdf("draft-product", "owner-a", self.store)

        partial = self.content.model_copy(update={"sections": self.content.sections[:2]})
        self.store.save_content(
            product_id="private-product", user_id="owner-a", content_payload=partial.model_dump(mode="json"),
            change_summary="Partial saved content",
        )
        with self.assertRaisesRegex(ProductPdfExportError, "incomplete"):
            export_saved_product_pdf("private-product", "owner-a", self.store)

    def test_unsafe_or_misplaced_saved_visual_is_rejected_without_modifying_product(self):
        self.store.save_visual_asset(
            product_id="private-product", user_id="owner-a", asset_type="icon", title="Unsafe",
            filename="unsafe.svg", mime_type="image/svg+xml", placement="cover", metadata={},
            content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        )
        save_pass_qa(
            self.store, "private-product", "owner-a", self.bp, self.content, self.product_inputs,
            template_id="modern_business", page_size="letter",
            assets=self.store.get_visual_assets("private-product", "owner-a"),
        )
        with self.assertRaisesRegex(ProductPdfExportError, "restricted SVG"):
            export_saved_product_pdf("private-product", "owner-a", self.store)
        self.assertIsNotNone(self.store.get("private-product", "owner-a"))

        self.store.delete_visual_asset(
            product_id="private-product", user_id="owner-a",
            asset_id=self.store.list_visual_assets("private-product", "owner-a")[-1]["asset_id"],
        )
        self.store.save_visual_asset(
            product_id="private-product", user_id="owner-a", asset_type="icon", title="Unplaced",
            filename="unplaced.svg", mime_type="image/svg+xml", placement="section:8", metadata={},
            content=create_visual_asset("icon", "Unplaced").content,
        )
        save_pass_qa(
            self.store, "private-product", "owner-a", self.bp, self.content, self.product_inputs,
            template_id="modern_business", page_size="letter",
            assets=self.store.get_visual_assets("private-product", "owner-a"),
        )
        with self.assertRaisesRegex(ProductPdfExportError, "not present"):
            export_saved_product_pdf("private-product", "owner-a", self.store)

    def test_qa_history_read_failure_locks_export_without_claiming_qa_pass(self):
        with patch.object(self.store, "list_qa_runs", side_effect=sqlite3.OperationalError("read unavailable")):
            with self.assertRaisesRegex(ProductPdfExportError, "QA history is unavailable"):
                export_saved_product_pdf("private-product", "owner-a", self.store)


if __name__ == "__main__":
    unittest.main()
