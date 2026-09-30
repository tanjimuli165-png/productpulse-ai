import hashlib
import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from app.product.product_schema import (
    BlueprintSection,
    ContentBlock,
    EvidenceReference,
    GeneratedSection,
    ProductBlueprint,
    ProductContent,
    ProductInputs,
)
from app.product.qa import final_verification, qa_snapshot_fingerprint, run_product_qa
from app.product.storage import ProductStore
from app.product.template_engine import render_product_html


EVIDENCE = EvidenceReference(
    evidence_id="EV-123456789ABC",
    source_title="Freelancer planning thread",
    source_type="forum",
    customer_language="I struggle to plan client work each week.",
    url="https://example.org/source",
)


def sample_blueprint() -> ProductBlueprint:
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
            BlueprintSection(title="Plan the Week", purpose="Create a focused client-work plan.", components=["steps", "exercise", "table"]),
            BlueprintSection(title="Review Progress", purpose="Review the weekly plan and adjust.", components=["checklist"]),
            BlueprintSection(title="Prepare Next Week", purpose="Choose a next action for the coming week.", components=["action_steps"]),
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


def sample_content() -> ProductContent:
    return ProductContent(
        product_title="Weekly Client Work Workbook",
        subtitle="A practical planning workbook",
        blueprint_fingerprint="a" * 64,
        sections=[
            GeneratedSection(
                section_index=0,
                title="Plan the Week",
                purpose="Create a focused client-work plan for this week.",
                blocks=[
                    ContentBlock(kind="steps", title="Start with one outcome", items=["List active client commitments", "Choose one weekly priority", "Block a focus period"]),
                    ContentBlock(kind="exercise", title="Weekly planning exercise", body="Choose a realistic client-work outcome and record the next focused action."),
                    ContentBlock(kind="table", title="Weekly planner", columns=["Day", "Client task"], rows=[["Monday", "Draft proposal"]]),
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


def sample_inputs() -> ProductInputs:
    return ProductInputs(
        product_title="Weekly Client Work Workbook",
        audience="Freelancers managing client work",
        problem="Freelancers struggle to plan focused client work every week.",
        promise="Plan focused client work with a repeatable weekly process.",
        evidence=[EVIDENCE],
    )


def make_preview(blueprint=None, content=None, inputs=None, assets=None):
    blueprint = blueprint or sample_blueprint()
    content = content or sample_content()
    inputs = inputs or sample_inputs()
    return render_product_html(
        content,
        "minimal_professional",
        "letter",
        subtitle=content.subtitle,
        product_type=blueprint.product_type,
        audience=blueprint.target_audience,
        evidence=inputs.evidence,
        visual_assets=assets or [],
    )


class ProductQATests(unittest.TestCase):
    def run_qa(self, blueprint=None, content=None, inputs=None, preview=None, assets=None):
        blueprint = blueprint or sample_blueprint()
        content = content or sample_content()
        inputs = inputs or sample_inputs()
        preview = preview if preview is not None else make_preview(blueprint, content, inputs, assets)
        return run_product_qa(
            blueprint,
            content,
            inputs,
            design={"template_id": "minimal_professional", "page_size": "letter"},
            visual_assets=assets or [],
            preview_html=preview,
        )

    def checks_by_name(self, result):
        return {check["name"]: check for check in result["checks"]}

    def test_clean_product_passes_available_checks_but_pdf_checks_are_explicitly_not_run(self):
        result = self.run_qa()
        checks = self.checks_by_name(result)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(checks["Empty / missing preview pages"]["status"], "PASS")
        self.assertEqual(checks["Preview headings"]["status"], "PASS")
        self.assertEqual(checks["Preview tables"]["status"], "PASS")
        self.assertEqual(checks["Indicative page labels"]["status"], "PASS")
        self.assertEqual(checks["Final PDF rendering"]["status"], "NOT RUN")
        self.assertIn("does not assemble or inspect the separately exported product PDF", " ".join(result["limitations"]))

    def test_missing_sections_repetition_and_negation_conflict_are_actionable(self):
        blueprint = sample_blueprint()
        content = ProductContent(
            product_title="Weekly Client Work Workbook",
            subtitle="A practical planning workbook",
            blueprint_fingerprint="a" * 64,
            sections=[
                GeneratedSection(
                    section_index=0,
                    title="Plan the Week",
                    purpose="Create a focused client-work plan for this week.",
                    blocks=[
                        ContentBlock(kind="paragraph", body="The weekly review is useful for planning client work each week."),
                        ContentBlock(kind="paragraph", body="The weekly review is not useful for planning client work each week."),
                        ContentBlock(kind="paragraph", body="Freelancers should review client tasks each week and select a focused priority for a useful weekly plan."),
                        ContentBlock(kind="paragraph", body="Freelancers should review client tasks each week and select a focused priority for a useful weekly plan."),
                    ],
                )
            ],
        )
        result = self.run_qa(blueprint, content)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Outline coverage"]["status"], "FLAG")
        self.assertEqual(checks["Repeated content"]["status"], "FLAG")
        self.assertEqual(checks["Contradiction candidates"]["status"], "FLAG")
        self.assertEqual(result["status"], "NEEDS REVISION")

    def test_grammar_claim_filler_and_unclear_instruction_rules_are_disclosed(self):
        content = sample_content().model_copy(deep=True)
        content.sections[0].blocks = [
            ContentBlock(kind="steps", items=["Do this", "Pick an outcome"]),
            ContentBlock(kind="paragraph", body="in today's fast-paced world, results are guaranteed for 100 percent of users!!"),
            ContentBlock(kind="paragraph", body="i suggest this.  Review it ."),
            *content.sections[0].blocks[2:],
        ]
        result = self.run_qa(content=content)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Grammar / formatting candidates"]["status"], "FLAG")
        self.assertEqual(checks["Unsupported-claim candidates"]["status"], "FLAG")
        self.assertEqual(checks["Common filler phrases"]["status"], "FLAG")
        self.assertEqual(checks["Unclear instruction candidates"]["status"], "FLAG")
        self.assertIn("threshold", checks["Repeated content"]["method"])
        self.assertIn("Heuristic", checks["Unsupported-claim candidates"]["method"])

    def test_problem_alignment_outcome_and_product_type_checks_can_flag(self):
        blueprint = sample_blueprint().model_copy(update={"desired_outcome": "Publish a profitable online store."})
        content = sample_content().model_copy(deep=True)
        content.sections = [GeneratedSection(
            section_index=0,
            title="General notes",
            purpose="Read this information.",
            blocks=[ContentBlock(kind="paragraph", body="Consider some ideas and reflect on them.")],
        )]
        result = self.run_qa(blueprint, content)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Problem concept coverage"]["status"], "REVIEW")
        self.assertEqual(checks["Outcome concept coverage"]["status"], "REVIEW")
        self.assertEqual(checks["Actionable components"]["status"], "FLAG")
        self.assertEqual(checks["Structure for selected product type"]["status"], "FLAG")

    def test_preview_mismatch_missing_visual_and_table_or_heading_issue_is_found(self):
        content = sample_content()
        bad_preview = '<html><main data-template-id="other" data-page-size="a4"><section class="dpe-page"><h1>Cover</h1><footer>1 / 3</footer></section></main></html>'
        assets = [{"asset_id": "v1", "title": "Cover visual", "mime_type": "image/png", "placement": "cover", "content": b"image-bytes"}]
        result = self.run_qa(content=content, preview=bad_preview, assets=assets)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Empty / missing preview pages"]["status"], "FLAG")
        self.assertEqual(checks["Preview headings"]["status"], "FLAG")
        self.assertEqual(checks["Saved visuals in preview"]["status"], "FLAG")
        self.assertEqual(checks["Preview layout settings"]["status"], "FLAG")

    def test_saved_visual_is_checked_as_preview_markup_not_pdf_embedding(self):
        asset = {"asset_id": "v1", "title": "Cover visual", "mime_type": "image/png", "placement": "cover", "content": b"image-bytes"}
        result = self.run_qa(assets=[asset])
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Saved visuals in preview"]["status"], "PASS")
        self.assertIn("does not test PDF", checks["Saved visuals in preview"]["method"])
        self.assertEqual(checks["Final PDF rendering"]["status"], "NOT RUN")

    def test_research_traceability_and_differentiation_checks_are_visible(self):
        blueprint = sample_blueprint()
        inputs = sample_inputs().model_copy(update={
            "research_backed": True,
            "market_context": [
                "Etsy: Weekly Client Planning Workbook | format=workbook | observed_price=$12"
            ],
            "differentiation": [
                "Structured handoff review with source-linked examples",
                "Decision prompts for client-specific planning",
            ],
        })
        enriched_evidence = EVIDENCE.model_copy(update={
            "source_excerpt": "I struggle to plan client work each week and keep handoffs visible."
        })
        inputs = inputs.model_copy(update={"evidence": [enriched_evidence]})
        content = sample_content().model_copy(deep=True)
        content.sections[0].blocks.append(
            ContentBlock(
                kind="reference",
                title="Research note",
                body="Use the supplied research excerpt as a grounding note.",
                evidence_ids=[enriched_evidence.evidence_id],
            )
        )
        preview = make_preview(blueprint, content, inputs)
        result = self.run_qa(blueprint, content, inputs, preview=preview)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Research evidence readiness"]["status"], "PASS")
        self.assertEqual(checks["Research evidence traceability"]["status"], "PASS")
        self.assertIn(checks["Research-language grounding"]["status"], {"PASS", "REVIEW"})
        self.assertEqual(checks["Market comparison context"]["status"], "PASS")
        self.assertEqual(checks["Differentiation captured"]["status"], "PASS")
        self.assertEqual(checks["Differentiation evidence signal"]["status"], "PASS")

    def test_missing_research_traceability_and_differentiation_stays_advisory(self):
        blueprint = sample_blueprint()
        inputs = sample_inputs().model_copy(update={
            "research_backed": True,
            "market_context": [],
            "differentiation": [],
        })
        result = self.run_qa(blueprint=blueprint, content=sample_content(), inputs=inputs)
        checks = self.checks_by_name(result)
        self.assertEqual(checks["Research evidence readiness"]["status"], "PASS")
        self.assertEqual(checks["Research evidence traceability"]["status"], "REVIEW")
        self.assertEqual(checks["Market comparison context"]["status"], "REVIEW")
        self.assertEqual(checks["Differentiation captured"]["status"], "REVIEW")
        self.assertEqual(checks["Differentiation evidence signal"]["status"], "REVIEW")

    def test_final_verification_can_pass_after_actual_pdf_is_supplied(self):
        from reportlab.pdfgen import canvas

        output = BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(72, 720, "Weekly Client Work Workbook")
        pdf.drawString(72, 700, "Plan focused client work.")
        pdf.save()

        blueprint = sample_blueprint()
        content = sample_content()
        inputs = sample_inputs()
        preview = make_preview(blueprint, content, inputs)
        result = final_verification(
            blueprint,
            content,
            inputs,
            design={"template_id": "minimal_professional", "page_size": "letter"},
            visual_assets=[],
            preview_html=preview,
            pdf_bytes=output.getvalue(),
        )
        checks = self.checks_by_name(result)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(checks["Final PDF rendering"]["status"], "NOT RUN")
        self.assertEqual(checks["Final PDF structural verification"]["status"], "PASS")
        self.assertEqual(
            result["verified_pdf_sha256"],
            hashlib.sha256(output.getvalue()).hexdigest(),
        )

    def test_fingerprint_changes_when_product_inputs_change(self):
        inputs_a = sample_inputs()
        inputs_b = inputs_a.model_copy(update={"promise": inputs_a.promise + " Add a concrete weekly review step."})

        first = qa_snapshot_fingerprint(
            blueprint(),
            content(),
            inputs=inputs_a,
            design={"template_id": "minimal_professional", "page_size": "letter"},
        )
        second = qa_snapshot_fingerprint(
            blueprint(),
            content(),
            inputs=inputs_b,
            design={"template_id": "minimal_professional", "page_size": "letter"},
        )

        self.assertNotEqual(first, second)


    def test_fingerprint_changes_when_content_design_visual_or_preview_changes(self):
        blueprint, content = sample_blueprint(), sample_content()
        base = qa_snapshot_fingerprint(blueprint, content, design={"template_id": "minimal_professional", "page_size": "letter"})
        changed_content = content.model_copy(update={"product_title": "Revised title"})
        changed = qa_snapshot_fingerprint(blueprint, changed_content, design={"template_id": "minimal_professional", "page_size": "letter"})
        changed_design = qa_snapshot_fingerprint(blueprint, content, design={"template_id": "clean_workbook", "page_size": "letter"})
        changed_preview = qa_snapshot_fingerprint(blueprint, content, design={"template_id": "minimal_professional", "page_size": "letter"}, preview_html="changed")
        self.assertEqual(len(base), 64)
        self.assertNotEqual(base, changed)
        self.assertNotEqual(base, changed_design)
        self.assertNotEqual(base, changed_preview)

    def test_qa_history_is_persisted_and_isolated_by_authenticated_owner(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "products.db")
            blueprint_payload = {"title": "Approved workbook"}
            blueprint_hash = hashlib.sha256(json.dumps(blueprint_payload, sort_keys=True).encode()).hexdigest()
            store.save(
                product_id="private-product", user_id="owner-a", source_report_id="r1", opportunity_index=0,
                opportunity_name="Workbook", source_payload={"report_id": "r1"}, inputs_payload={"problem": "plan"},
                blueprint_payload=blueprint_payload, status="approved",
            )
            store.save_content(
                product_id="private-product", user_id="owner-a",
                content_payload={"product_title": "Workbook", "blueprint_fingerprint": blueprint_hash, "sections": []},
                change_summary="Initial content",
            )
            fingerprint = "f" * 64
            result = {"status": "NEEDS REVISION", "checks": [{"name": "Outline coverage", "status": "FLAG"}]}
            saved = store.save_qa_run(
                product_id="private-product", user_id="owner-a", snapshot_fingerprint=fingerprint, result_payload=result
            )
            self.assertEqual(saved["result_payload"], result)
            self.assertEqual(len(store.list_qa_runs("private-product", "owner-a")), 1)
            self.assertEqual(store.list_qa_runs("private-product", "owner-b"), [])
            with self.assertRaises(PermissionError):
                store.save_qa_run(product_id="private-product", user_id="owner-b", snapshot_fingerprint=fingerprint, result_payload=result)
            with self.assertRaises(ValueError):
                store.save_qa_run(product_id="private-product", user_id="", snapshot_fingerprint=fingerprint, result_payload=result)
            with self.assertRaises(ValueError):
                store.save_qa_run(product_id="private-product", user_id="owner-a", snapshot_fingerprint="bad", result_payload=result)


if __name__ == "__main__":
    unittest.main()
