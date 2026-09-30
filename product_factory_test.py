import os
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from app.database.models import Evidence, Opportunity, ProblemSignal, Report
from app.product.content_generator import ContentGenerationError, SECTION_CONTENT_JSON_SCHEMA, generate_section_content
from app.product.product_schema import ContentBlock, EvidenceReference, GeneratedSection, PRODUCT_TYPES, ProductBlueprint, ProductContent, ProductInputs
from app.product.storage import ProductStore
from app.product.strategy import BlueprintGenerationError, BLUEPRINT_JSON_SCHEMA, generate_blueprint, recommend_product_types
from app.ui.product_generator import build_product_inputs


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.request = None
        self.requests = []

    def create(self, **kwargs):
        self.request = kwargs
        self.requests.append(kwargs)
        if isinstance(self.content, list):
            index = min(len(self.requests) - 1, len(self.content) - 1)
            response_content = self.content[index]
        else:
            response_content = self.content
        msg = SimpleNamespace(content=response_content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(completions=FakeCompletions(content))


def sample_blueprint(product_type="Workbook"):
    return {
        "title": "Weekly Meal Prep Clarity Workbook",
        "subtitle": "A research-backed concept to test with home meal planners",
        "target_audience": "People planning meals who report losing time to weekly prep.",
        "core_problem": "Weekly meal planning takes too much time.",
        "desired_outcome": "A repeatable weekly meal-prep routine.",
        "promise": "Plan the next week with a guided sequence and reusable tracker.",
        "product_type": product_type,
        "recommended_types": ["Workbook", "Playbook"],
        "recommendation_reason": "The opportunity points to practical planning and guided implementation.",
        "outline": [
            {"title": "Start Here", "purpose": "Choose a useful planning outcome.", "components": ["paragraph", "worksheet"]},
            {"title": "Weekly Workflow", "purpose": "Set up the planning sequence.", "components": ["steps", "reference"]},
            {"title": "Review", "purpose": "Reflect and identify a next step.", "components": ["reflection", "action_steps"]},
        ],
        "estimated_page_count": 18,
        "exercises": ["Five-minute planning diagnostic"],
        "checklists": ["Weekly prep checklist"],
        "worksheets": ["Meal planning worksheet"],
        "examples": ["Example week with limited preparation time"],
        "templates": ["Weekly planning grid"],
        "bonuses": ["One-page printable reference"],
        "design_direction": "Clean workbook layout with clear writing space and restrained blue accents.",
    }


def make_opportunity(problem_text):
    return Opportunity(
        name="Meal Prep Workbook",
        audience=f"People working on meal prep who report: {problem_text.lower()}",
        promise="Build a repeatable weekly routine.",
        format=["workbook", "checklists"],
        problem_fit=0.5,
        willingness_to_pay=0,
        competition_gap=0.5,
        evidence_strength=0.2,
        validation_score=42,
        pricing={"starter": "$19", "core": "$29", "premium": "$49"},
        components=[],
        differentiation=["Short guided process"],
        risks=[],
        next_steps=["Interview target users"],
    )


class ProductFactoryTests(unittest.TestCase):
    def test_inputs_only_include_evidence_linked_to_selected_problem(self):
        url = "https://example.test/post-1"
        problem_text = "I struggle to plan meals each week and waste too much time deciding what to cook."
        report = Report(
            id="r1",
            topic="Meal prep",
            evidence=[
                Evidence(source="Reddit", title="Meal prep discussion", text=problem_text, url=url),
                Evidence(source="Web", title="Unrelated article", text="Different subject", url="https://example.test/other"),
            ],
            problems=[ProblemSignal(problem=problem_text, customer_language=[problem_text], evidence_urls=[url])],
            opportunities=[make_opportunity(problem_text)],
        )
        inputs = build_product_inputs(report, report.opportunities[0])
        self.assertEqual(inputs.problem, problem_text)
        self.assertEqual([item.url for item in inputs.evidence], [url])
        self.assertEqual(inputs.evidence[0].source_type, "Reddit")
        self.assertEqual(inputs.evidence[0].customer_language, problem_text)
        self.assertNotIn("Unrelated article", str(inputs.model_dump()))
        recommended, reason = recommend_product_types(inputs)
        self.assertEqual(recommended[0], "Workbook")
        self.assertTrue(reason)

    def test_generation_uses_strict_schema_and_selected_type(self):
        fake = FakeClient(json.dumps(sample_blueprint(product_type="Guide")))
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable routine.",
            format_hints=["workbook", "workspace"],
            evidence=[],
            validation_steps=["Interview five target users"],
        )
        result = generate_blueprint(inputs, "Workbook", client=fake, model="test-model")
        self.assertIsInstance(result, ProductBlueprint)
        self.assertEqual(result.product_type, "Workbook")
        self.assertEqual(result.recommended_types, ["Workbook", "Playbook"])
        request = fake.chat.completions.request
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(request["response_format"]["json_schema"]["schema"], BLUEPRINT_JSON_SCHEMA)
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["max_completion_tokens"], 3600)
        self.assertIn("never as proof of demand", request["messages"][0]["content"])
        self.assertEqual(len(PRODUCT_TYPES), 12)

    def test_blueprint_schema_rejects_unsupported_types_and_missing_outline(self):
        payload = sample_blueprint()
        payload["product_type"] = "Market Winner"
        with self.assertRaises(ValueError):
            ProductBlueprint.model_validate(payload)
        payload = sample_blueprint()
        payload["outline"] = []
        with self.assertRaises(ValueError):
            ProductBlueprint.model_validate(payload)

    def test_storage_is_user_scoped_and_versions_changes(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "test.db")
            source = {"report_id": "report-1", "topic": "Meal prep"}
            inputs = {"product_title": "Meal Prep Workbook"}
            blueprint = sample_blueprint()
            saved = store.save(
                product_id="p1", user_id="user-a", source_report_id="report-1", opportunity_index=0,
                opportunity_name="Meal Prep Workbook", source_payload=source, inputs_payload=inputs,
                blueprint_payload=blueprint, status="draft", change_summary="AI generated", create_version=True,
            )
            self.assertEqual(saved["versions"][0]["version_number"], 1)
            self.assertIsNone(store.get("p1", "user-b"))
            edited = {**blueprint, "title": "Edited Meal Prep Workbook"}
            saved = store.save(
                product_id="p1", user_id="user-a", source_report_id="report-1", opportunity_index=0,
                opportunity_name="Meal Prep Workbook", source_payload=source, inputs_payload=inputs,
                blueprint_payload=edited, status="approved", change_summary="User edit", create_version=True,
            )
            self.assertEqual(saved["status"], "approved")
            self.assertEqual(saved["blueprint_payload"]["title"], edited["title"])
            self.assertEqual([item["version_number"] for item in saved["versions"]], [1, 2])
            self.assertEqual(store.recent_for_opportunity("user-a", "report-1", 0)[0]["product_id"], "p1")
            with self.assertRaises(PermissionError):
                store.save(
                    product_id="p1", user_id="user-b", source_report_id="report-1", opportunity_index=0,
                    opportunity_name="Meal Prep Workbook", source_payload=source, inputs_payload=inputs,
                    blueprint_payload=blueprint,
                )

    def test_saved_input_changes_invalidate_existing_generated_content(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "test.db")
            blueprint = ProductBlueprint.model_validate(sample_blueprint())
            blueprint_payload = blueprint.model_dump(mode="json")
            input_payload = {
                "product_title": "Meal Prep Workbook",
                "audience": "People planning meals",
                "problem": "Weekly meal planning takes too much time.",
                "promise": "Create a repeatable weekly meal-prep routine.",
            }
            saved = store.save(
                product_id="p-input-change",
                user_id="user-a",
                source_report_id="report-1",
                opportunity_index=0,
                opportunity_name="Meal Prep Workbook",
                source_payload={},
                inputs_payload=input_payload,
                blueprint_payload=blueprint_payload,
                status="approved",
            )
            fingerprint = hashlib.sha256(
                json.dumps(blueprint_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            content = ProductContent(
                product_title=blueprint.title,
                blueprint_fingerprint=fingerprint,
                sections=[
                    GeneratedSection(
                        section_index=0,
                        title=blueprint.outline[0].title,
                        purpose=blueprint.outline[0].purpose,
                        blocks=[ContentBlock(kind="paragraph", title="Grounded content", body="Meal planning content.")],
                    ),
                ],
            )
            store.save_content(
                product_id="p-input-change",
                user_id="user-a",
                content_payload=content.model_dump(mode="json"),
                change_summary="Initial generated content",
            )
            changed = dict(input_payload)
            changed["promise"] = "Create a faster weekly meal-prep routine."
            updated = store.save(
                product_id="p-input-change",
                user_id="user-a",
                source_report_id="report-1",
                opportunity_index=0,
                opportunity_name="Meal Prep Workbook",
                source_payload={},
                inputs_payload=changed,
                blueprint_payload=blueprint_payload,
                status="draft",
                change_summary="Updated product inputs",
            )
            self.assertEqual(updated["status"], "draft")
            self.assertIsNone(updated["content_payload"])


    def test_missing_provider_credentials_returns_setup_error(self):
        import os
        previous_key = os.environ.pop("OPENAI_API_KEY", None)
        previous_product_key = os.environ.pop("PRODUCT_BUILDER_API_KEY", None)
        try:
            inputs = ProductInputs(product_title="Draft", audience="Audience", problem="Problem", promise="Promise")
            result = generate_blueprint(inputs, "Guide")
            self.assertIsInstance(result, ProductBlueprint)
            self.assertEqual(result.product_type, "Guide")
        finally:
            if previous_key is not None:
                os.environ["OPENAI_API_KEY"] = previous_key
            if previous_product_key is not None:
                os.environ["PRODUCT_BUILDER_API_KEY"] = previous_product_key

    def test_section_generation_requests_only_one_outline_section_and_allows_known_references(self):
        payload = {
            "blocks": [
                {"kind": "steps", "title": "Plan the week", "body": "Use these steps to make a first draft.", "items": ["Choose three meals", "List needed ingredients"], "columns": [], "rows": [], "evidence_ids": []},
                {"kind": "reference", "title": "Research signal", "body": "This source contains the linked problem language.", "items": [], "columns": [], "rows": [], "evidence_ids": ["EV-0123456789AB"]},
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        evidence = [EvidenceReference(
            evidence_id="EV-0123456789AB", source_title="Meal planning discussion",
            source_type="Reddit", customer_language="Weekly planning takes time.",
            url="https://example.test/meal-planning",
        )]

        result = generate_section_content(blueprint, 1, evidence, client=fake, model="test-model")

        self.assertEqual(result.section_index, 1)
        self.assertEqual(result.title, blueprint.outline[1].title)
        self.assertEqual(len(result.blocks), 2)
        request = fake.chat.completions.request
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["max_completion_tokens"], 3200)
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(request["response_format"]["json_schema"]["schema"], SECTION_CONTENT_JSON_SCHEMA)
        self.assertIn("exactly the one requested section", request["messages"][0]["content"])
        request_body = json.loads(request["messages"][1]["content"])
        self.assertEqual(request_body["section_to_write"]["index"], 1)
        self.assertEqual(len(request_body["approved_blueprint"]["outline"]), len(blueprint.outline))
        self.assertEqual(request_body["available_research_references"][0]["evidence_id"], "EV-0123456789AB")

    def test_section_generation_passes_product_fit_context_to_provider(self):
        payload = {
            "blocks": [
                {
                    "kind": "steps",
                    "title": "Plan meal decisions",
                    "body": "Use a repeatable weekly meal plan because weekly meal planning takes too much time; this routine helps create a repeatable weekly meal-prep routine and keeps grocery choices visible.",
                    "items": ["Choose the meals for the week", "List the grocery items", "Record the next cooking action"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Research signal",
                    "body": "This source contains the linked problem language.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": ["EV-0123456789AB"],
                },
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint_data = sample_blueprint()
        blueprint_data["outline"][0]["components"].append("reference")
        blueprint = ProductBlueprint.model_validate(blueprint_data)
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
            differentiation=["Includes a practical grocery decision workflow."],
            validation_steps=["Test the workflow with target readers."],
            research_backed=True,
            market_context=["Etsy listing: weekly meal planner workbook"],
        )
        evidence = [EvidenceReference(
            evidence_id="EV-0123456789AB", source_title="Meal planning discussion",
            source_type="Reddit", customer_language="Weekly planning takes time.",
            url="https://example.test/meal-planning",
        )]
        result = generate_section_content(
            blueprint, 1, evidence, client=fake, model="test-model", product_inputs=inputs
        )
        self.assertEqual(result.section_index, 1)
        request_body = json.loads(fake.chat.completions.request["messages"][1]["content"])
        self.assertTrue(request_body["product_fit_context"]["research_backed"])
        self.assertEqual(request_body["product_fit_context"]["differentiation"], inputs.differentiation)
        self.assertEqual(request_body["product_fit_context"]["validation_steps"], inputs.validation_steps)
        self.assertEqual(request_body["product_fit_context"]["market_context"], inputs.market_context)

    def test_section_generation_retries_after_grounding_failure(self):
        good_payload = {
            "blocks": [
                {
                    "kind": "steps",
                    "title": "Plan meal decisions",
                    "body": "Use a weekly meal planning routine to reduce the time spent deciding what to cook and create a repeatable weekly meal-prep routine.",
                    "items": ["Choose meals for the week", "List grocery items", "Record the next cooking action"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Research note",
                    "body": "A supplied research note for the approved product.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
            ]
        }
        bad_payload = {
            "blocks": [
                {
                    "kind": "steps",
                    "title": "General steps",
                    "body": "Follow a simple process and review what happened before repeating it.",
                    "items": ["Start with the task", "Review the result", "Choose what to do next"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Research note",
                    "body": "A supplied research note for the approved product.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
            ]
        }
        fake = FakeClient([json.dumps(bad_payload), json.dumps(good_payload)])
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
        )
        result = generate_section_content(
            blueprint, 1, client=fake, model="test-model", product_inputs=inputs
        )
        self.assertEqual(result.section_index, 1)
        self.assertEqual(len(fake.chat.completions.requests), 2)
        second_body = json.loads(fake.chat.completions.requests[1]["messages"][1]["content"])
        self.assertIn("generation_feedback", second_body)
        self.assertIn("grounding check failed", second_body["generation_feedback"]["previous_validation_error"])

    def test_reference_block_copy_does_not_count_as_creator_material_use(self):
        payload = {
            "blocks": [
                {
                    "kind": "paragraph",
                    "title": "General planning context",
                    "body": "Use a weekly meal planning routine to reduce time and move toward a repeatable weekly meal-prep routine.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "worksheet",
                    "title": "Weekly planning worksheet",
                    "body": "Record the current planning situation and the next routine you will test.",
                    "items": ["Current situation:", "Next action:"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Creator note",
                    "body": "Creator note: batch-cooking three staple proteins on Sunday reduces weekday prep decisions.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint_data = sample_blueprint()
        blueprint_data["outline"][0]["components"].append("reference")
        blueprint = ProductBlueprint.model_validate(blueprint_data)
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
            reference_material="Creator note: batch-cooking three staple proteins on Sunday reduces weekday prep decisions.",
        )
        with self.assertRaises(ContentGenerationError) as ctx:
            generate_section_content(
                blueprint, 0, client=fake, model="test-model", product_inputs=inputs
            )
        self.assertIn("creator-supplied reference material", str(ctx.exception))


    def test_section_generation_rejects_first_section_that_ignores_creator_reference_material(self):
        payload = {
            "blocks": [
                {
                    "kind": "paragraph",
                    "title": "General planning context",
                    "body": "Use a weekly meal planning routine to reduce time and move toward a repeatable weekly routine.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "worksheet",
                    "title": "Weekly planning worksheet",
                    "body": "Record the current planning situation and the next routine you will test.",
                    "items": ["Current situation:", "Next action:"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
            reference_material="Creator note: batch-cooking three staple proteins on Sunday reduces weekday prep decisions.",
        )
        with self.assertRaises(ContentGenerationError) as ctx:
            generate_section_content(
                blueprint, 0, client=fake, model="test-model", product_inputs=inputs
            )
        self.assertIn("creator-supplied reference material", str(ctx.exception))


    def test_section_generation_rejects_output_without_product_topic(self):
        payload = {
            "blocks": [
                {
                    "kind": "steps",
                    "title": "General planning steps",
                    "body": "Use a weekly planning routine to organize tasks and move toward completing the weekly plan.",
                    "items": ["Organize the tasks", "Review the plan", "Choose the next action"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Research note",
                    "body": "A supplied research note for the approved product.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                }
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly planning helps organize tasks.",
            promise="Complete the weekly plan with clear next actions.",
        )
        with self.assertRaises(ContentGenerationError) as ctx:
            generate_section_content(
                blueprint, 1, client=fake, model="test-model", product_inputs=inputs
            )
        self.assertIn("the product topic", str(ctx.exception))


    def test_section_generation_rejects_provider_output_that_is_too_generic(self):
        payload = {
            "blocks": [
                {
                    "kind": "steps",
                    "title": "General steps",
                    "body": "Follow a simple process and review what happened before repeating it.",
                    "items": ["Start with the task", "Review the result", "Choose what to do next"],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                },
                {
                    "kind": "reference",
                    "title": "Research note",
                    "body": "A supplied research note for the approved product.",
                    "items": [],
                    "columns": [],
                    "rows": [],
                    "evidence_ids": [],
                }
            ]
        }
        fake = FakeClient(json.dumps(payload))
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
        )
        with self.assertRaises(ContentGenerationError) as ctx:
            generate_section_content(
                blueprint, 1, client=fake, model="test-model", product_inputs=inputs
            )
        self.assertIn("grounding check failed", str(ctx.exception))

    def test_content_generation_rejects_citations_not_in_supplied_evidence(self):
        payload = {"blocks": [{
            "kind": "reference", "title": "Unsupported source", "body": "This source was not provided.",
            "items": [], "columns": [], "rows": [], "evidence_ids": ["EV-NOT-SUPPLIED"],
        }]}
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        with self.assertRaisesRegex(ContentGenerationError, "Section content generation failed"):
            generate_section_content(blueprint, 0, [], client=FakeClient(json.dumps(payload)))

    def test_offline_generation_is_subject_to_product_grounding_validation(self):
        blueprint = ProductBlueprint.model_validate(sample_blueprint())
        inputs = ProductInputs(
            product_title="Meal Prep Workbook",
            audience="People planning meals",
            problem="Weekly meal planning takes too much time.",
            promise="Create a repeatable weekly meal-prep routine.",
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "PRODUCT_BUILDER_API_KEY": ""}):
            with self.assertRaises(ContentGenerationError):
                generate_section_content(
                    blueprint, 0, [], client=None, product_inputs=inputs
                )

    def test_missing_provider_credentials_returns_content_setup_error(self):
        import os
        previous_key = os.environ.pop("OPENAI_API_KEY", None)
        previous_product_key = os.environ.pop("PRODUCT_BUILDER_API_KEY", None)
        try:
            blueprint = ProductBlueprint.model_validate(sample_blueprint())
            result = generate_section_content(blueprint, 0)
            self.assertIsNotNone(result.blocks)
        finally:
            if previous_key is not None:
                os.environ["OPENAI_API_KEY"] = previous_key
            if previous_product_key is not None:
                os.environ["PRODUCT_BUILDER_API_KEY"] = previous_product_key

    def test_content_component_and_content_schema_validate_table_shapes_and_sections(self):
        with self.assertRaisesRegex(ValueError, "row must match"):
            ContentBlock(kind="table", columns=["Task", "When"], rows=[["Plan"]])
        block = ContentBlock(kind="table", columns=["Task", "When"], rows=[["Plan", "Sunday"]])
        self.assertEqual(block.rows[0], ["Plan", "Sunday"])
        content = ProductContent(
            product_title="Meal Prep Workbook",
            blueprint_fingerprint="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            sections=[{
                "section_index": 0, "title": "Start Here", "purpose": "Set a goal.",
                "blocks": [{"kind": "paragraph", "body": "Pick a practical weekly goal."}],
            }],
        )
        self.assertFalse(content.is_complete(3))
        self.assertTrue(content.is_complete(1))
        with self.assertRaisesRegex(ValueError, "duplicate sections"):
            ProductContent.model_validate({**content.model_dump(), "sections": [content.sections[0].model_dump(), content.sections[0].model_dump()]})

    def test_content_storage_is_user_scoped_approved_and_versioned(self):
        with TemporaryDirectory() as directory:
            store = ProductStore(Path(directory) / "content.db")
            blueprint = ProductBlueprint.model_validate(sample_blueprint()).model_dump(mode="json")
            fingerprint = hashlib.sha256(json.dumps(blueprint, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            store.save(
                product_id="content-product", user_id="user-a", source_report_id="report-1", opportunity_index=0,
                opportunity_name="Meal Prep Workbook", source_payload={"report_id": "report-1"},
                inputs_payload={"product_title": "Meal Prep Workbook"}, blueprint_payload=blueprint,
                status="approved",
            )
            content = {
                "product_title": "Meal Prep Workbook", "blueprint_fingerprint": fingerprint,
                "sections": [{"section_index": 0, "title": "Start Here", "purpose": "Set a goal.", "blocks": [{"kind": "paragraph", "body": "Set a practical goal."}]}],
            }
            with self.assertRaisesRegex(ValueError, "current approved blueprint revision"):
                store.save_content(
                    product_id="content-product", user_id="user-a",
                    content_payload={**content, "blueprint_fingerprint": "0" * 64},
                    change_summary="Stale draft",
                )
            saved = store.save_content(product_id="content-product", user_id="user-a", content_payload=content, change_summary="Generated first section")
            self.assertEqual(saved["content_payload"], content)
            self.assertEqual(saved["content_versions"][0]["version_number"], 1)
            updated = {**content, "sections": content["sections"] + [{"section_index": 1, "title": "Weekly Workflow", "purpose": "Plan the week.", "blocks": [{"kind": "steps", "items": ["Choose meals"]}]}]}
            saved = store.save_content(product_id="content-product", user_id="user-a", content_payload=updated, change_summary="Generated second section")
            self.assertEqual([v["version_number"] for v in saved["content_versions"]], [1, 2])
            self.assertIsNone(store.get("content-product", "user-b"))
            with self.assertRaises(PermissionError):
                store.save_content(product_id="content-product", user_id="user-b", content_payload=updated, change_summary="Wrong owner")

    def test_content_storage_migrates_existing_product_tables(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            import sqlite3
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE products (
                    product_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,source_report_id TEXT NOT NULL,
                    opportunity_index INTEGER NOT NULL,opportunity_name TEXT NOT NULL,source_payload TEXT NOT NULL,
                    inputs_payload TEXT NOT NULL,blueprint_payload TEXT,status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
                conn.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    "legacy", "user-a", "report-1", 0, "Legacy product", "{}", "{}", None, "draft", "now", "now",
                ))
            store = ProductStore(path)
            loaded = store.get("legacy", "user-a")
            self.assertIsNone(loaded["content_payload"])
            with sqlite3.connect(path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
            self.assertIn("content_payload", columns)


    def test_competition_gap_is_neutral_without_gap_evidence(self):
        from app.product.architect import architect_opportunities

        problem = ProblemSignal(
            problem="People struggle to organize client onboarding steps.",
            customer_language=["People struggle to organize client onboarding steps."],
            evidence_items=1,
            urgency=0.1,
        )
        opportunities = architect_opportunities(
            "Client onboarding",
            [problem],
            {"score": 0.0},
            {"status": "insufficient_evidence", "gaps": [], "score": 0.0},
            evidence_count=1,
            marketplace_gaps=[],
        )
        self.assertEqual(len(opportunities), 1)
        self.assertEqual(opportunities[0].competition_gap, 0.0)
        self.assertEqual(opportunities[0].competition_gap_status, "insufficient_evidence")

if __name__ == "__main__":
    unittest.main()