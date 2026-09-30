from app.product.content_generator import generate_section_content
from app.product.product_schema import ProductInputs, ProductContent
from app.product.qa import run_product_qa
from app.product.strategy import _local_blueprint, clean_product_inputs


def _rendered_text(sections):
    return " ".join(
        " ".join(
            [block.title, block.body, *block.items, *[" ".join(row) for row in block.rows]]
        )
        for section in sections
        for block in section.blocks
    ).lower()


def _make_inputs(case):
    return ProductInputs(
        product_title=case["title"],
        audience=case["audience"],
        problem=case["problem"],
        promise=case["promise"],
        format_hints=[case.get("format", "Workbook")],
    )


def test_local_generation_is_topic_and_section_aware_across_multiple_products():
    cases = [
        {
            "title": "Meal Prep Clarity Kit",
            "audience": "Busy adults who want a simpler kitchen routine",
            "problem": "Meal planning, grocery decisions, and batch cooking feel scattered and time-consuming.",
            "promise": "Create a realistic weekly meal-prep routine and make grocery and cooking decisions more clearly.",
        },
        {
            "title": "Exam Study Planner",
            "audience": "High school students preparing for exams",
            "problem": "Study time gets scattered across subjects and students struggle to keep a realistic review routine.",
            "promise": "Plan weekly study blocks, track review progress, and adjust the schedule when priorities change.",
        },
        {
            "title": "Consultant Onboarding Workbook",
            "audience": "Independent consultants onboarding new clients",
            "problem": "Client onboarding steps are inconsistent, details get missed, and the first week takes too much manual coordination.",
            "promise": "Create a repeatable client onboarding workflow with clear steps, handoffs, and review points.",
        },
    ]

    topic_texts = {}
    signatures = []
    for case in cases:
        blueprint = _local_blueprint(_make_inputs(case), "Workbook")
        sections = [generate_section_content(blueprint, i) for i in range(len(blueprint.outline))]
        text = _rendered_text(sections)
        topic_texts[case["title"]] = text

        if "Meal" in case["title"]:
            assert "meal" in text and ("grocery" in text or "cooking" in text)
        elif "Exam" in case["title"]:
            assert "study" in text and ("exam" in text or "review" in text)
        else:
            assert "onboarding" in text and ("client" in text or "consultant" in text)

        assert "still need" in text
        signatures.append([tuple(block.kind for block in section.blocks) for section in sections])

    assert len({repr(signature) for signature in signatures}) == 1
    assert topic_texts[cases[0]["title"]] != topic_texts[cases[1]["title"]]
    assert topic_texts[cases[1]["title"]] != topic_texts[cases[2]["title"]]


def test_generic_local_fallback_avoids_near_duplicate_blocks():
    inputs = ProductInputs(
        product_title="Weekly Project Planner",
        audience="Freelance designers",
        problem="Project tasks and deadlines get scattered across notes and messages.",
        promise="Plan work in one place and keep deadlines visible.",
        format_hints=["Workbook"],
    )
    blueprint = _local_blueprint(inputs, "Workbook")
    sections = [generate_section_content(blueprint, i) for i in range(len(blueprint.outline))]
    content = ProductContent(
        product_title=blueprint.title,
        subtitle=blueprint.subtitle,
        blueprint_fingerprint="a" * 64,
        sections=sections,
    )
    result = run_product_qa(
        blueprint,
        content,
        inputs,
        design={"template_id": "minimal_professional", "page_size": "letter"},
        preview_html=None,
    )
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["Repeated content"]["status"] == "PASS", checks["Repeated content"]["issues"]

def test_creator_reference_material_is_used_in_local_generation():
    inputs = ProductInputs(
        product_title="Client Onboarding Checklist",
        audience="Independent consultants",
        problem="Client onboarding is inconsistent and key details are easy to miss.",
        promise="Create a repeatable onboarding sequence and capture essential handoff details.",
        format_hints=["Checklist"],
        reference_material="Our studio uses a three-part kickoff note: goals, access details, and owner names. Review the note before the first client call.",
    )
    blueprint = _local_blueprint(inputs, "Checklist")
    section = generate_section_content(blueprint, 0, reference_material=inputs.reference_material)
    text = _rendered_text([section])
    assert "kickoff" in text
    assert "goals" in text or "access" in text


def test_research_text_cleanup_removes_elongation_and_trims():
    inputs = ProductInputs(
        product_title="Meal Prep Clarity Kit",
        audience="People working on meal prep who say waaayyyyy too much food is left over.",
        problem="I'm so tired of spending time looking for recipes every week and trying to figure out what to cook.",
        promise="Make weekly meal prep simpler and clearer.",
        format_hints=["Workbook"],
    )
    cleaned = clean_product_inputs(inputs)
    assert "waayyyyy" not in cleaned.audience.lower()
    assert "waay" in cleaned.audience.lower()
    assert cleaned.problem.startswith("I'm so tired")


def test_local_generation_preserves_component_contract():
    inputs = ProductInputs(
        product_title="Weekly Project Planner",
        audience="Freelance designers",
        problem="Project tasks and deadlines get scattered across notes and messages.",
        promise="Plan work in one place and keep deadlines visible.",
        format_hints=["Planner"],
    )
    blueprint = _local_blueprint(inputs, "Planner")
    sections = [generate_section_content(blueprint, i) for i in range(len(blueprint.outline))]
    for index, section in enumerate(sections):
        assert {block.kind for block in section.blocks} == {
            kind.lower() for kind in blueprint.outline[index].components
        }


def test_blueprint_rejects_unknown_content_components():
    inputs = ProductInputs(
        product_title="Test Product",
        audience="A defined audience",
        problem="A clear problem that needs solving.",
        promise="A concrete intended outcome.",
    )
    blueprint = _local_blueprint(inputs, "Workbook")
    payload = blueprint.model_dump(mode="json")
    payload["outline"][0]["components"] = ["paragraph", "unknown_renderer_block"]
    from pydantic import ValidationError

    try:
        type(blueprint).model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("Blueprint accepted an unknown content component.")


def test_qa_separates_advisory_checks_from_hard_blockers():
    inputs = ProductInputs(
        product_title="Client Onboarding Checklist",
        audience="Independent consultants",
        problem="Client onboarding steps are inconsistent and details get missed.",
        promise="Create a repeatable onboarding sequence with clear handoffs.",
        format_hints=["Checklist"],
        research_backed=True,
        market_context=[
            "Etsy: Client Onboarding Template | format=checklist | observed_price=$12"
        ],
        differentiation=[
            "Includes a structured handoff review and source-linked evidence."
        ],
    )
    blueprint = _local_blueprint(inputs, "Checklist")
    sections = [
        generate_section_content(blueprint, i, inputs.evidence, inputs.reference_material)
        for i in range(len(blueprint.outline))
    ]
    content = ProductContent(
        product_title=blueprint.title,
        subtitle=blueprint.subtitle,
        blueprint_fingerprint="a" * 64,
        sections=sections,
    )
    result = run_product_qa(
        blueprint,
        content,
        inputs,
        design={"template_id": "minimal_professional", "page_size": "letter"},
        preview_html=None,
    )
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["Market comparison context"]["status"] == "PASS"
    assert checks["Differentiation captured"]["status"] == "PASS"
    assert "Problem concept coverage" in checks
    assert "Outcome concept coverage" in checks
    assert result["status"] in {"PASS", "NEEDS REVISION"}
