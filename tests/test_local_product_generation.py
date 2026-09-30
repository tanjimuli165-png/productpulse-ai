from app.product.content_generator import generate_section_content
from app.product.strategy import _local_blueprint
from app.product.product_schema import ProductInputs


def test_meal_prep_local_generation_is_specific_and_section_aware():
    inputs = ProductInputs(
        product_title="Meal Prep Clarity Kit",
        audience="Busy adults who want a simpler kitchen routine",
        problem="Meal planning, grocery decisions, and batch cooking feel scattered and time-consuming.",
        promise="Create a realistic weekly meal-prep routine and make grocery and cooking decisions more clearly.",
        format_hints=["Workbook"],
        differentiation=["Practical kitchen routine"],
        validation_steps=["Review generated blueprint", "Check claims against supplied material"],
    )
    blueprint = _local_blueprint(inputs, "Workbook")

    sections = [
        generate_section_content(blueprint, i)
        for i in range(len(blueprint.outline))
    ]

    rendered = [
        " ".join(
            [block.title, block.body, *block.items, *[" ".join(row) for row in block.rows]]
        ).lower()
        for section in sections
        for block in section.blocks
    ]
    combined = " ".join(rendered)

    assert "meal prep" in combined
    assert "grocery" in combined or "cooking" in combined

    section_signatures = [
        (section.title, tuple(block.kind for block in section.blocks))
        for section in sections
    ]
    assert len(set(section_signatures)) == len(section_signatures)

    for index, section in enumerate(sections):
        planned = {kind.lower() for kind in blueprint.outline[index].components}
        actual = {block.kind.lower() for block in section.blocks}
        assert actual == planned
