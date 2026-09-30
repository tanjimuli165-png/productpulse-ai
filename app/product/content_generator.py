from __future__ import annotations

import json
import os
from typing import Any

from app.product.product_schema import (
    BlueprintSection,
    EvidenceReference,
    GeneratedSection,
    ProductBlueprint,
)
from app.product.strategy import BlueprintGenerationError, _provider_client


class ContentGenerationError(RuntimeError):
    """A safe, user-facing error raised when section content cannot be generated."""


CONTENT_BLOCK_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "paragraph", "steps", "example", "exercise", "checklist",
                "worksheet", "table", "reflection", "action_steps", "reference",
            ],
        },
        "title": {"type": "string", "maxLength": 120},
        "body": {"type": "string", "maxLength": 5000},
        "items": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 30},
        "columns": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 8},
        "rows": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 8},
            "maxItems": 20,
        },
        "evidence_ids": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 10},
    },
    "required": ["kind", "title", "body", "items", "columns", "rows", "evidence_ids"],
    "additionalProperties": False,
}

SECTION_CONTENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocks": {"type": "array", "items": CONTENT_BLOCK_JSON_SCHEMA, "minItems": 1, "maxItems": 24},
    },
    "required": ["blocks"],
    "additionalProperties": False,
}

CONTENT_SYSTEM_PROMPT = """You are a careful digital-product content writer. Write complete, useful content for exactly the one requested section of an already-approved product blueprint. Follow its purpose and planned components; choose a suitable mix of concise explanations, actionable steps, clearly labeled illustrative examples, exercises, checklists, worksheets, tables, reflection prompts, and next actions as appropriate. Return structured content blocks, not markdown or a whole-book draft.

Treat the research opportunity as an evidence-supported hypothesis that still needs validation. Never promise sales, results, guaranteed demand, or outcomes. Do not invent statistics, research findings, testimonials, customer quotes, named sources, or validation results. Any example scenario must be explicitly described as hypothetical or illustrative. You may cite only the exact evidence IDs in the supplied source list; include their IDs in a reference block only when relevant. If there are no suitable sources, do not create citations. Avoid unsupported factual claims, filler, and repetition. Be practical, plain-language, inclusive, and specific enough that the reader can act."""


def _token_limit(model: str) -> dict[str, int]:
    """Use the token parameter expected by the selected model family."""
    lowered = model.lower()
    if lowered.startswith("gemini-") or lowered.startswith("claude-"):
        return {"max_tokens": 3200}
    return {"max_completion_tokens": 3200}



def _meal_prep_local_section_content(
    blueprint: ProductBlueprint,
    section_index: int,
) -> GeneratedSection:
    """Topic-specific offline content for the meal-prep workbook path."""
    section = blueprint.outline[section_index]
    planned = [str(kind).strip().lower() for kind in section.components]
    title = section.title

    def block(kind: str, block_title: str, body: str = "", *, items=None, columns=None, rows=None):
        return {
            "kind": kind,
            "title": block_title,
            "body": body,
            "items": items or [],
            "columns": columns or [],
            "rows": rows or [],
            "evidence_ids": [],
        }

    blocks = []
    section_key = title.lower()
    if section_key == "baseline":
        if "paragraph" in planned:
            blocks.append(block(
                "paragraph",
                "Map your current meal-prep friction",
                "Look at one normal week of meals. Notice where time goes: choosing recipes, deciding what to buy, preparing ingredients, cooking in batches, storing portions, or ending up with food you do not want to eat later.",
            ))
        if "worksheet" in planned:
            blocks.append(block(
                "worksheet",
                "Weekly meal-prep baseline",
                "Fill this in before changing your routine.",
                items=[
                    "Meals I actually need to plan for this week:",
                    "Recipes or meals I already know I will eat:",
                    "Groceries I usually forget or overbuy:",
                    "Prep or cooking time I can realistically use:",
                    "Food I often end up wasting or not enjoying:",
                ],
            ))
    elif section_key == "guided lessons":
        if "paragraph" in planned:
            blocks.append(block(
                "paragraph",
                "Build a smaller, repeatable meal plan",
                "The goal is not to find the perfect recipe collection. Build a weekly menu that matches your schedule, grocery needs, cooking capacity, and food preferences.",
            ))
        if "steps" in planned:
            blocks.append(block(
                "steps",
                "A simple weekly meal-prep workflow",
                "Use the same sequence each week, then adjust it when your schedule or preferences change.",
                items=[
                    "Choose a small set of meals you are realistically willing to eat this week.",
                    "Combine ingredients into one grocery list and remove items you already have.",
                    "Pick one or two prep tasks that save effort later, such as washing produce or cooking a base ingredient.",
                    "Portion and store food in a way that makes the next meal easy to choose.",
                    "Review what was actually eaten before planning the next week.",
                ],
            ))
        if "example" in planned:
            blocks.append(block(
                "example",
                "Illustrative example: a lower-friction week",
                "Hypothetical example: instead of planning seven different recipes, a reader chooses three repeatable meals, creates one grocery list, prepares a shared ingredient once, and leaves room for one flexible meal. The point is to reduce decisions, not to follow a perfect menu.",
            ))
    elif section_key == "exercises":
        if "exercise" in planned:
            blocks.append(block(
                "exercise",
                "Plan one realistic week",
                "Create a meal-prep plan you could actually follow next week.",
                items=[
                    "Choose three to five meals that fit your schedule and food preferences.",
                    "Write the ingredients you need and remove duplicates from the list.",
                    "Choose one prep block and define exactly what you will prepare.",
                    "Decide how the prepared food will be stored and when you expect to eat it.",
                    "After the week, note what was eaten, skipped, or disliked.",
                ],
            ))
        if "worksheet" in planned:
            blocks.append(block(
                "worksheet",
                "Meal-prep experiment notes",
                "Use this page while testing the plan.",
                items=[
                    "Meals I chose:",
                    "Grocery decisions I simplified:",
                    "Prep task I completed:",
                    "What made the routine easier or harder:",
                    "What I will change next week:",
                ],
            ))
    elif section_key == "worksheets":
        if "worksheet" in planned:
            blocks.append(block(
                "worksheet",
                "Meal and grocery planning page",
                "Fill in only the meals and ingredients that fit your actual week.",
                items=[
                    "Meal or recipe I want:",
                    "Key ingredients:",
                    "What I already have:",
                    "What needs to go on the grocery list:",
                    "Prep task and planned day:",
                ],
            ))
        if "table" in planned:
            blocks.append(block(
                "table",
                "Weekly meal-prep planner",
                "Use the table to connect each meal with its ingredients and prep task.",
                columns=["Meal / recipe", "Key ingredients", "Prep task", "Planned day"],
                rows=[
                    ["Meal 1", "List the main ingredients", "What will I prep?", "Day / meal"],
                    ["Meal 2", "List the main ingredients", "What will I prep?", "Day / meal"],
                    ["Meal 3", "List the main ingredients", "What will I prep?", "Day / meal"],
                    ["Flexible meal", "What can use what is left?", "Minimal prep", "Day / meal"],
                ],
            ))
    elif section_key == "review":
        if "reflection" in planned:
            blocks.append(block(
                "reflection",
                "Review the week honestly",
                "Use what actually happened to improve the next plan. A missed meal or unused ingredient is information about the routine, not a reason to force the same plan again.",
                items=[
                    "Which meals were easiest to choose and prepare?",
                    "Where did grocery decisions or recipe searching still take too much time?",
                    "What food was left unused or not enjoyable?",
                    "Which prep task was worth repeating?",
                ],
            ))
        if "action_steps" in planned:
            blocks.append(block(
                "action_steps",
                "Adjust next week's routine",
                "Make a small change based on what you observed.",
                items=[
                    "Keep one meal or prep task that clearly fit the week.",
                    "Remove one choice, recipe, or grocery step that created unnecessary friction.",
                    "Choose one concrete change to test in the next weekly plan.",
                ],
            ))

    # If the user changes section names in the approved blueprint, preserve the
    # component contract but still use meal-prep language instead of the old
    # generic fallback recipe.
    if not blocks:
        for kind in planned:
            if kind == "paragraph":
                blocks.append(block("paragraph", f"{title}: meal-prep focus", "Apply this section to your weekly meals, grocery choices, recipe decisions, and realistic prep routine."))
            elif kind in {"steps", "action_steps"}:
                blocks.append(block(kind, f"{title}: meal-prep actions", "Work through these actions with one real week.", items=[
                    "Choose the meals you realistically want this week.",
                    "Simplify the grocery list and prep tasks.",
                    "Review what worked and adjust the next week.",
                ]))
            elif kind == "checklist":
                blocks.append(block("checklist", f"{title}: meal-prep checklist", "Check each item when completed.", items=[
                    "Meals chosen", "Grocery list simplified", "Prep task completed", "Next change recorded",
                ]))
            elif kind in {"exercise", "worksheet", "reflection"}:
                blocks.append(block(kind, f"{title}: meal-prep working page", "Use this page with your own meals, groceries, prep tasks, and review notes.", items=[
                    "What I am planning:", "What I need:", "What I will test next:",
                ]))
            elif kind == "example":
                blocks.append(block("example", f"{title}: meal-prep example", "Illustrative example: simplify one week's meals, grocery list, and prep tasks, then review what was actually useful."))
            elif kind == "table":
                blocks.append(block("table", f"{title}: meal-prep table", "Track the details for your own week.", columns=["Meal", "Ingredients", "Prep", "Review"], rows=[
                    ["Meal 1", "Ingredients", "Prep task", "What changed?"],
                    ["Meal 2", "Ingredients", "Prep task", "What changed?"],
                ]))

    return GeneratedSection(
        section_index=section_index,
        title=section.title,
        purpose=section.purpose,
        blocks=blocks,
    )


def _local_section_content(
    blueprint: ProductBlueprint,
    section_index: int,
    evidence: list[EvidenceReference],
    reference_material: str = "",
) -> GeneratedSection:
    """Deterministic no-API-key content that follows the section's planned components."""
    section = blueprint.outline[section_index]
    topic = blueprint.title.strip()
    problem = blueprint.core_problem.strip()
    outcome = blueprint.desired_outcome.strip()
    audience = blueprint.target_audience.strip()
    title_lower = section.title.lower()
    planned = [str(kind).strip().lower() for kind in section.components]
    purpose = section.purpose.strip()

    def has(*kinds: str) -> bool:
        return any(kind in planned for kind in kinds)

    def block(kind: str, title: str, body: str = "", *, items=None, columns=None, rows=None, evidence_ids=None):
        return {
            "kind": kind,
            "title": title,
            "body": body,
            "items": items or [],
            "columns": columns or [],
            "rows": rows or [],
            "evidence_ids": evidence_ids or [],
        }

    blocks: list[dict] = []

    # Build only the block types requested by this format's blueprint section.
    # This is the key safeguard against every product becoming the same
    # paragraph + steps + worksheet document.
    if has("paragraph"):
        # The PDF already prints the section purpose directly below its heading.
        # Keep this block reader-facing and topic-specific instead of repeating
        # the same metadata sentence a second time.
        blocks.append(block(
            "paragraph",
            f"{section.title}: key context",
            f"For {audience}, connect this section to the real situation behind {problem.lower()}. Use the material here to make progress toward {outcome.lower()} without assuming a guaranteed result.",
        ))

    if has("steps", "action_steps"):
        kind = "action_steps" if has("action_steps") and not has("steps") else "steps"
        if blueprint.product_type.strip().lower() == "planner":
            if "next period" in title_lower:
                items = [
                    f"Choose the next planning period for {topic.lower()}.",
                    f"Carry forward only the priorities that still support {outcome.lower()}.",
                    "Assign a realistic next action and a review point for each priority.",
                ]
            elif "priority" in title_lower:
                items = [
                    "List the tasks that matter for the current planning period.",
                    "Mark which task needs attention first and why.",
                    "Set a small, realistic next action for each priority.",
                ]
            else:
                items = [
                    f"Choose one planning period for {topic.lower()}.",
                    f"Write the most important actions connected to {outcome.lower()}.",
                    "Set a review point so the plan can be adjusted rather than treated as fixed.",
                ]
            body = "Use this planning sequence to turn the product goal into a workable schedule."
        else:
            items = [
                f"Choose one real situation where {problem.lower()} appears.",
                f"Apply the {section.title.lower()} approach to that situation and work toward {outcome.lower()}.",
                "Record what happened, what remains unclear, and the next adjustment to test.",
            ]
            body = f"Use these steps with one real example from your {topic.lower()}."
        blocks.append(block(kind, f"{section.title}: practical steps", body, items=items))

    if has("checklist"):
        blocks.append(block(
            "checklist",
            f"{section.title}: completion checklist",
            "Mark each item only when it is actually completed.",
            items=[
                f"I used a real example related to {problem.lower()}.",
                f"I completed the {section.title.lower()} task rather than only reading it.",
                f"I recorded a concrete next action connected to {outcome.lower()}.",
            ],
        ))

    if has("example"):
        blocks.append(block(
            "example",
            f"{section.title}: illustrative example",
            f"Hypothetical example: a reader working on {topic.lower()} notices that {problem.lower()} is getting in the way. They apply the section approach, record what changes, and decide what to test next. This example is illustrative, not a claim about actual results.",
        ))

    if has("exercise"):
        blocks.append(block(
            "exercise",
            f"{section.title}: practice exercise",
            f"Use one real situation connected to {problem.lower()} and complete the exercise before moving on.",
            items=[
                "Describe the situation with specific details.",
                f"Apply the section method to move toward {outcome.lower()}.",
                "Write down the result and one adjustment for another attempt.",
            ],
        ))

    if has("worksheet"):
        blocks.append(block(
            "worksheet",
            f"{section.title}: working page",
            f"Complete these prompts for your own {topic.lower()}.",
            items=[
                "My real situation or starting point:",
                "The specific action I will take:",
                "What I need to prepare:",
                "What I learned or need to test next:",
            ],
        ))

    if has("reflection"):
        if blueprint.product_type.strip().lower() == "journal":
            if "progress review" in title_lower:
                prompts = [
                    "What pattern do I notice across my recent entries?",
                    "What felt easier, harder, or different over this period?",
                    "What is one small change I want to carry into the next period?",
                ]
            elif "next steps" in title_lower:
                prompts = [
                    "What do I want to continue from this journal period?",
                    "What would make the next step feel realistic and specific?",
                    "What will I check in my next entry?",
                ]
            else:
                prompts = [
                    "What am I noticing right now?",
                    "What feels most important to explore honestly?",
                    "What question do I want to return to later?",
                ]
            reflection_body = "Write from your own experience. There is no required answer; use the prompts to notice, explore, and decide what matters next."
        else:
            prompts = [
                "What worked or became clearer?",
                "Where did I still experience friction?",
                "What will I keep, change, or test next?",
            ]
            reflection_body = "Use the prompts to review your work rather than simply restating the section."
        blocks.append(block(
            "reflection",
            f"{section.title}: reflection",
            reflection_body,
            items=prompts,
        ))

    if has("table"):
        if blueprint.product_type.strip().lower() == "journal":
            columns = ["Entry / period", "What I noticed", "What I want to explore", "Next check-in"]
            rows = [
                ["Today / this period", "What stood out?", "What question remains?", "When will I revisit it?"],
                ["A meaningful moment", "What did I learn?", "What deserves more attention?", "What will I notice next?"],
                ["Progress review", "What pattern appeared?", "What do I want to carry forward?", "When will I reflect again?"],
            ]
            body = "Use this table to capture patterns across entries rather than replacing the journal writing itself."
        elif blueprint.product_type.strip().lower() == "planner":
            columns = ["Time / item", "Priority", "Planned action", "Review"]
            rows = [
                ["Planning period", "High / medium / low", "What needs to happen?", "When will I check it?"],
                [topic[:80], "What matters most?", "What is the next action?", "What changed?"],
                [outcome[:80], "What supports the goal?", "What will I schedule?", "What needs adjustment?"],
            ]
            body = "Use this table to schedule priorities and leave a clear point for review."
        else:
            columns = ["Item", "Current state", "Next action"]
            rows = [
                [section.title, "What is true now?", "What will I do next?"],
                [problem[:80], "What friction remains?", "What will I test?"],
                [outcome[:80], "What progress would look like?", "When will I review it?"],
            ]
            body = "Use the table with your own product-specific details."
        blocks.append(block(
            "table",
            f"{section.title}: planning table",
            body,
            columns=columns,
            rows=rows,
        ))

    if has("reference"):
        if reference_material.strip():
            blocks.append(block(
                "reference",
                "Creator reference notes",
                "Review these creator-supplied notes as context and verify important claims before publication.",
                items=[reference_material.strip()[:500]],
            ))
        elif evidence:
            blocks.append(block(
                "reference",
                "Research references",
                "These supplied records support the product hypothesis; verify original sources before publication.",
                evidence_ids=[e.evidence_id for e in evidence[:5]],
            ))

    # Custom section components may be sparse. Give the user a useful, section-
    # specific paragraph rather than silently inventing unrelated block types.
    if not blocks:
        blocks.append(block(
            "paragraph",
            section.title,
            f"Work through this custom section using its stated purpose: {purpose}. Apply it to {topic.lower()}, keep claims grounded in the supplied material, and record what you need to test next.",
        ))

    return GeneratedSection(
        section_index=section_index,
        title=section.title,
        purpose=section.purpose,
        blocks=blocks,
    )

def _validate_component_contract(section: BlueprintSection, generated: GeneratedSection) -> None:
    """Keep provider output aligned with the approved section component contract."""
    planned = {str(kind).strip().lower() for kind in section.components if str(kind).strip()}
    actual = {str(block.kind).strip().lower() for block in generated.blocks if str(block.kind).strip()}
    missing = planned - actual
    unexpected = actual - planned
    if missing:
        raise ValueError(
            "Generated content is missing planned component(s): "
            + ", ".join(sorted(missing))
            + "."
        )
    if unexpected:
        raise ValueError(
            "Generated content added unplanned component(s): "
            + ", ".join(sorted(unexpected))
            + "."
        )


def generate_section_content(
    blueprint: ProductBlueprint,
    section_index: int,
    evidence: list[EvidenceReference] | None = None,
    reference_material: str = "",
    *,
    client=None,
    model: str | None = None,
) -> GeneratedSection:
    """Generate validated content for one outline section, never the whole product."""
    if not 0 <= section_index < len(blueprint.outline):
        raise ValueError("Choose a section that exists in the approved blueprint.")
    section: BlueprintSection = blueprint.outline[section_index]
    references = evidence or []
    topic_text = " ".join(
        [
            blueprint.title,
            blueprint.core_problem,
            blueprint.desired_outcome,
            blueprint.target_audience,
        ]
    ).lower()
    if "meal prep" in topic_text or "meal-prep" in topic_text or "grocery" in topic_text or "batch cooking" in topic_text:
        return _meal_prep_local_section_content(blueprint, section_index)
    allowed_ids = {reference.evidence_id for reference in references}
    model = model or os.getenv("PRODUCT_BUILDER_MODEL", "gpt-5-mini")
    try:
        if client is None and not (os.getenv("PRODUCT_BUILDER_API_KEY") or os.getenv("OPENAI_API_KEY")):
            return _local_section_content(
                blueprint,
                section_index,
                references,
                reference_material=reference_material,
            )
        if client is None:
            client = _provider_client()
    except BlueprintGenerationError as exc:
        message = str(exc).replace("blueprint generation", "product content generation")
        raise ContentGenerationError(message) from exc

    request_data = {
        "approved_blueprint": {
            "title": blueprint.title,
            "subtitle": blueprint.subtitle,
            "product_type": blueprint.product_type,
            "target_audience": blueprint.target_audience,
            "core_problem": blueprint.core_problem,
            "desired_outcome": blueprint.desired_outcome,
            "promise": blueprint.promise,
            "outline": [item.model_dump(mode="json") for item in blueprint.outline],
            "relevant_components": {
                "exercises": blueprint.exercises,
                "checklists": blueprint.checklists,
                "worksheets": blueprint.worksheets,
                "examples": blueprint.examples,
                "templates": blueprint.templates,
            },
        },
        "section_to_write": {
            "index": section_index,
            "title": section.title,
            "purpose": section.purpose,
            "planned_components": section.components,
        },
        "available_research_references": [item.model_dump(mode="json") for item in references],
        "creator_reference_material": reference_material,
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CONTENT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request_data, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "product_section_content",
                    "strict": True,
                    "schema": SECTION_CONTENT_JSON_SCHEMA,
                },
            },
            **_token_limit(model),
        )
        raw = response.choices[0].message.content
        if not raw:
            raise ValueError("The provider returned an empty section.")
        payload = json.loads(raw)
        generated = GeneratedSection(
            section_index=section_index,
            title=section.title,
            purpose=section.purpose,
            blocks=payload["blocks"],
        )
        _validate_component_contract(section, generated)
        used_ids = {evidence_id for block in generated.blocks for evidence_id in block.evidence_ids}
        unknown_ids = used_ids - allowed_ids
        if unknown_ids:
            raise ValueError("Generated content cited a source that was not supplied.")
        return generated
    except ContentGenerationError:
        raise
    except Exception as exc:
        name = type(exc).__name__
        raise ContentGenerationError(
            f"Section content generation failed ({name}). Check the configured provider/model and try again. Previously saved sections are preserved."
        ) from exc
