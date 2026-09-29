import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.product.product_schema import (
    DESIGN_TEMPLATE_IDS,
    ContentBlock,
    EvidenceReference,
    GeneratedSection,
    ProductContent,
    ProductDesign,
)
from app.product.storage import ProductStore
from app.product.template_engine import (
    PAGE_SIZES,
    TEMPLATES,
    design_details,
    render_product_html,
    template_recommendation,
)


def sample_content() -> ProductContent:
    return ProductContent(
        product_title="Meal Prep <Starter>",
        blueprint_fingerprint="0123456789abcdef0123456789abcdef",
        sections=[
            GeneratedSection(
                section_index=0,
                title="Plan the week",
                purpose="Create a repeatable starting routine.",
                blocks=[
                    ContentBlock(kind="steps", title="First pass", items=["Choose meals", "Write a short list"]),
                    ContentBlock(kind="checklist", title="Before shopping", items=["Check the pantry"]),
                    ContentBlock(kind="table", title="Weekly grid", columns=["Day", "Meal"], rows=[["Monday", "Soup"]]),
                    ContentBlock(kind="worksheet", title="Reflection", body="What would make this easier?"),
                    ContentBlock(kind="reference", title="Source", body="Linked research note.", evidence_ids=["EV-VALID"]),
                ],
            )
        ],
    )


class ProductTemplateTests(unittest.TestCase):
    def test_three_templates_define_distinct_page_designs_and_layouts(self):
        self.assertEqual({item.template_id for item in TEMPLATES}, set(DESIGN_TEMPLATE_IDS))
        self.assertEqual(len({item.palette for item in TEMPLATES}), 3)
        self.assertEqual(len({item.typography for item in TEMPLATES}), 3)
        self.assertTrue(all(item.margins and item.table_treatment and item.checkbox_treatment for item in TEMPLATES))
        self.assertEqual(TEMPLATES[0].layout_for("table"), "data_table")
        self.assertEqual(TEMPLATES[1].layout_for("table"), "banded_table")
        self.assertEqual(TEMPLATES[2].layout_for("table"), "writing_table")

    def test_recommendation_is_deterministic_but_overrideable(self):
        self.assertEqual(template_recommendation("Workbook")[0], "clean_workbook")
        self.assertEqual(template_recommendation("Playbook")[0], "modern_business")
        self.assertEqual(template_recommendation("Checklist")[0], "minimal_professional")
        for template_id in DESIGN_TEMPLATE_IDS:
            self.assertTrue(design_details(template_id)["name"])

    def test_same_content_renders_distinct_versions_and_supports_letter_and_a4(self):
        content = sample_content()
        evidence = [EvidenceReference(
            evidence_id="EV-VALID", source_title="Meal planning discussion", source_type="Reddit",
            customer_language="A linked source", url="https://example.test/source",
        )]
        minimal = render_product_html(content, "minimal_professional", "letter", subtitle="A revised planning guide", evidence=evidence)
        workbook = render_product_html(content, "clean_workbook", "a4", evidence=evidence)
        self.assertIn('data-template-id="minimal_professional"', minimal)
        self.assertIn('data-template-id="clean_workbook"', workbook)
        self.assertIn('data-page-size="a4"', workbook)
        self.assertNotEqual(minimal, workbook)
        self.assertIn("data_table", minimal)
        self.assertIn("writing_table", workbook)
        self.assertIn("Monday", workbook)
        self.assertIn("A revised planning guide", minimal)
        self.assertIn("href=\"https://example.test/source\"", workbook)
        self.assertEqual(PAGE_SIZES["letter"]["label"], "US Letter")
        self.assertEqual(PAGE_SIZES["a4"]["label"], "A4")

    def test_user_content_is_escaped_and_non_http_source_links_are_not_rendered(self):
        content = ProductContent(
            product_title="<script>alert('x')</script>",
            blueprint_fingerprint="0123456789abcdef",
            sections=[GeneratedSection(
                section_index=0,
                title="<img src=x onerror=alert(1)>",
                purpose="safe & sound",
                blocks=[ContentBlock(kind="reference", title="Evidence", body="Citation", evidence_ids=["EV-BAD"])],
            )],
        )
        html = render_product_html(
            content, "minimal_professional", evidence=[EvidenceReference(
                evidence_id="EV-BAD", source_title="A source", source_type="Web",
                customer_language="Text", url="javascript:alert(1)",
            )],
        )
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("safe &amp; sound", html)
        self.assertNotIn("href=\"javascript:", html)

    def test_design_schema_rejects_unknown_template_and_page_size(self):
        with self.assertRaises(ValueError):
            ProductDesign(template_id="unlimited_templates")
        with self.assertRaises(ValueError):
            ProductDesign(page_size="legal")

    def test_design_migration_defaults_and_account_scoped_save(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "design.db"
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE products (
                    product_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,source_report_id TEXT NOT NULL,
                    opportunity_index INTEGER NOT NULL,opportunity_name TEXT NOT NULL,source_payload TEXT NOT NULL,
                    inputs_payload TEXT NOT NULL,blueprint_payload TEXT,status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
                conn.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    "p1", "account-a", "r1", 0, "Meal prep", "{}", "{}", None, "draft", "now", "now",
                ))
            store = ProductStore(path)
            existing = store.get("p1", "account-a")
            self.assertEqual(existing["design_template_id"], "minimal_professional")
            self.assertEqual(existing["page_size"], "letter")
            saved = store.save_design(
                product_id="p1", user_id="account-a",
                design=ProductDesign(template_id="clean_workbook", page_size="a4"),
            )
            self.assertEqual(saved["design_template_id"], "clean_workbook")
            self.assertEqual(saved["page_size"], "a4")
            self.assertIsNone(store.get("p1", "account-b"))
            with self.assertRaises(PermissionError):
                store.save_design(product_id="p1", user_id="account-b", design={"template_id": "modern_business", "page_size": "letter"})
            with self.assertRaises(LookupError):
                store.save_design(product_id="missing", user_id="account-a", design={"template_id": "modern_business", "page_size": "letter"})


if __name__ == "__main__":
    unittest.main()
