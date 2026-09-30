from __future__ import annotations

import json
import os
import re
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

Treat the research opportunity as an evidence-supported hypothesis that still needs validation. Never promise sales, results, guaranteed demand, or outcomes. Do not invent statistics, research findings, testimonials, customer quotes, named sources, or validation results. Any example scenario must be explicitly described as hypothetical or illustrative. Use supplied source excerpts as grounding context and cite only the exact evidence IDs present in the source list; never invent IDs. If there are no suitable sources, do not create citations. Avoid unsupported factual claims, filler, and repetition. Be practical, plain-language, inclusive, and specific enough that the reader can act."""


def _token_limit(model: str) -> dict[str, int]:
    """Use the token parameter expected by the selected model family."""
    lowered = model.lower()
    if lowered.startswith("gemini-") or lowered.startswith("claude-"):
        return {"max_tokens": 3200}
    return {"max_completion_tokens": 3200}



def _local_section_content(
    blueprint: ProductBlueprint,
    section_index: int,
    evidence: list[EvidenceReference],
    reference_material: str = "",
) -> GeneratedSection:
    """Generate useful offline content from the actual topic/problem/outcome contract."""
    section = blueprint.outline[section_index]
    topic = " ".join(blueprint.title.split()).strip()
    problem = " ".join(blueprint.core_problem.split()).strip()
    outcome = " ".join(blueprint.desired_outcome.split()).strip()
    audience = " ".join(blueprint.target_audience.split()).strip()
    purpose = " ".join(section.purpose.split()).strip()
    title_lower = section.title.lower()
    planned = list(dict.fromkeys(str(kind).strip().lower() for kind in section.components if str(kind).strip()))

    def compact(value: str, limit: int = 220) -> str:
        value = " ".join((value or "").split()).strip()
        if len(value) <= limit:
            return value
        clipped = value[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return (clipped or value[:limit]).rstrip() + "…"

    def terms(value: str, limit: int = 8) -> list[str]:
        stop = {
            "about", "after", "again", "also", "because", "before", "being", "between",
            "could", "does", "each", "from", "have", "into", "more", "most", "other",
            "should", "some", "such", "than", "that", "their", "there", "these", "they",
            "this", "through", "under", "what", "when", "where", "which", "with", "your",
            "reader", "people", "someone", "things", "really", "want", "needs", "need",
        }
        result: list[str] = []
        for word in re.findall(r"[a-z0-9]+", (value or "").lower()):
            if len(word) < 4 or word in stop or word in result:
                continue
            result.append(word)
            if len(result) >= limit:
                break
        return result

    def related_reference_notes() -> list[str]:
        raw = " ".join((reference_material or "").split()).strip()
        if not raw:
            return []
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", raw) if part.strip()]
        focus = set(terms(f"{topic} {problem} {outcome} {section.title}", 12))
        scored: list[tuple[int, int, str]] = []
        for index, sentence in enumerate(sentences):
            score = len(focus & set(terms(sentence, 16)))
            scored.append((score, -index, compact(sentence, 260)))
        scored.sort(reverse=True)
        selected: list[str] = []
        for _, _, sentence in scored:
            if sentence and sentence not in selected:
                selected.append(sentence)
            if len(selected) >= 2:
                break
        return selected

    notes = related_reference_notes()
    problem_terms = terms(problem, 7)
    outcome_terms = terms(outcome, 7)
    focus = ", ".join(problem_terms[:4]) or compact(problem, 90)
    outcome_focus = ", ".join(outcome_terms[:4]) or compact(outcome, 90)
    section_role = compact(purpose, 260)

    def stage() -> str:
        low = section.title.lower()
        if any(token in low for token in ("start", "intro", "baseline", "setup", "how to use", "before you start", "instructions")):
            return "setup"
        if any(token in low for token in ("review", "progress", "next", "result", "completion", "retrospective")):
            return "review"
        if any(token in low for token in ("lesson", "concept", "workflow", "core", "guide", "main")):
            return "practice"
        return "apply"

    section_stage = stage()

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

    blocks: list[dict[str, Any]] = []
    for kind in planned:
        if kind == "paragraph":
            paragraph_bodies = {
                "setup": (
                    f"Start by making the current {focus} situation visible for {audience}. "
                    f"Capture the starting point, constraints, and what useful progress toward {outcome_focus} would look like."
                ),
                "practice": (
                    f"Use this section to work through the {section.title.lower()} method with {audience}. "
                    f"Keep {focus} connected to the intended direction of {outcome_focus}, and note where the method is clear or difficult to apply."
                ),
                "apply": (
                    f"Turn the ideas in {section.title.lower()} into a concrete output for {audience}. "
                    f"Address {focus} directly, keep the target of {outcome_focus} visible, and record any assumption that needs another test."
                ),
                "review": (
                    f"Use this section to inspect what actually happened after working on {focus}. "
                    f"Compare the observed result with {outcome_focus}, separate evidence from interpretation, and decide what should change next."
                ),
            }
            blocks.append(block(
                "paragraph",
                f"{section.title}: focused context",
                paragraph_bodies[section_stage],
            ))

        elif kind in {"steps", "action_steps"}:
            if section_stage == "setup":
                items = [
                    f"Describe one real situation where {focus} appears.",
                    f"Define what useful progress would look like for {outcome_focus}.",
                    f"Choose the smallest practical starting action for {section.title.lower()}.",
                    "Record one constraint, missing input, or uncertainty before continuing.",
                ]
            elif section_stage == "practice":
                items = [
                    f"Break {section.title.lower()} into one clear decision or action.",
                    f"Apply that action to a real example connected to {focus}.",
                    "Check what changed, what stayed difficult, and what still needs evidence.",
                    "Adjust the approach before repeating it with another example.",
                ]
            elif section_stage == "review":
                items = [
                    f"Review what actually happened during {section.title.lower()}.",
                    f"Identify what helped move the work toward {outcome_focus}.",
                    "Separate an observed result from an assumption or interpretation.",
                    "Choose one concrete change to test next.",
                ]
            else:
                items = [
                    f"Choose one real example related to {focus}.",
                    f"Complete the {section.title.lower()} task using the approved purpose as the guide.",
                    f"Record the action, decision, or output that supports {outcome_focus}.",
                    "Write one adjustment to test before reusing the same approach.",
                ]
            blocks.append(block(
                kind,
                f"{section.title}: practical workflow",
                "Work with one real example at a time so the reader can see what changes and what remains uncertain.",
                items=items,
            ))

        elif kind == "checklist":
            checklist_items = {
                "setup": [
                    f"I wrote down a specific starting situation involving {focus}.",
                    "I recorded the main constraint or missing input.",
                    f"I defined what useful progress toward {outcome_focus} would look like.",
                    "I marked assumptions that still need evidence.",
                ],
                "practice": [
                    f"I worked through one example involving {focus}.",
                    f"I followed the {section.title.lower()} method rather than only reading it.",
                    "I noted where the method was unclear or difficult to apply.",
                    "I recorded one adjustment for the next example.",
                ],
                "apply": [
                    f"I created or completed the expected output for {section.title.lower()}.",
                    f"I addressed a concrete part of {focus}.",
                    f"I checked the output against {outcome_focus}.",
                    "I recorded what still needs testing or refinement.",
                ],
                "review": [
                    "I reviewed what actually happened, not just what I expected to happen.",
                    f"I identified evidence relevant to {focus}.",
                    f"I compared the result with {outcome_focus}.",
                    "I chose one change or follow-up check for the next cycle.",
                ],
            }
            blocks.append(block(
                "checklist",
                f"{section.title}: completion check",
                "Mark an item only after the work is actually complete.",
                items=checklist_items[section_stage],
            ))

        elif kind == "example":
            example_openers = {
                "setup": (
                    f"Hypothetical example: a {audience.lower()} reader first maps a real situation involving {focus}. "
                    f"They define a practical target related to {outcome_focus} before choosing what to change."
                ),
                "practice": (
                    f"Hypothetical example: while working on {topic.lower()}, a {audience.lower()} reader applies the {section.title.lower()} method to one real case. "
                    "They note what was useful, where the method broke down, and what they would try next."
                ),
                "apply": (
                    f"Hypothetical example: a {audience.lower()} reader turns the {section.title.lower()} work into a concrete output addressing {focus}. "
                    f"They check it against {outcome_focus} and record what still needs refinement."
                ),
                "review": (
                    f"Hypothetical example: after working on {topic.lower()}, a {audience.lower()} reader reviews what changed around {focus}. "
                    f"They compare the observed result with {outcome_focus} and choose a small next adjustment."
                ),
            }
            blocks.append(block(
                "example",
                f"{section.title}: illustrative example",
                example_openers[section_stage] + " This example is illustrative only and is not evidence that the same result will occur for everyone.",
            ))

        elif kind == "exercise":
            blocks.append(block(
                "exercise",
                f"{section.title}: practice task",
                f"Use one real situation connected to {focus} and complete the task before moving on.",
                items=[
                    "Write the starting situation in one or two specific sentences.",
                    f"Apply the section method to move toward {outcome_focus}.",
                    "Capture the result, friction, or unanswered question.",
                    "Write one change you would make on a second attempt.",
                ],
            ))

        elif kind == "worksheet":
            worksheet_items = {
                "setup": [
                    f"Current situation to map for {focus}:",
                    "Constraints, resources, or missing information:",
                    f"Useful progress toward {outcome_focus} would look like:",
                    "The first realistic action I can take:",
                    "What I still need to verify:",
                ],
                "practice": [
                    f"Case example I am working through for {focus}:",
                    f"The {section.title.lower()} method I am applying:",
                    "What happened during the practice:",
                    "Where the method was unclear or difficult:",
                    f"How this connects to {outcome_focus}:",
                ],
                "apply": (
                    [
                        f"Scenario I will practice for {focus}:",
                        "Inputs or details needed for the exercise:",
                        "Decision or action I will take:",
                        f"What I expect to learn about {outcome_focus}:",
                        "What I would change in a second attempt:",
                    ]
                    if "exercise" in title_lower
                    else [
                        f"Concrete output or decision I need to create for {focus}:",
                        "Source material, inputs, or constraints to use:",
                        "Action, decision, or material completed:",
                        f"How I will check the output against {outcome_focus}:",
                        "What still needs testing or refinement:",
                    ]
                ),
                "review": [
                    f"Observed result while addressing {focus}:",
                    "Helpful factors and remaining friction:",
                    f"What the result suggests about {outcome_focus}:",
                    "Which assumption remains unverified:",
                    "The next change or follow-up check:",
                ],
            }
            worksheet_body = {
                "setup": "Use this page to establish the starting point before changing the approach.",
                "practice": "Use this page while applying the method to one concrete example.",
                "apply": (
                    "Use this page as a hands-on practice exercise before moving to the next section."
                    if "exercise" in title_lower
                    else "Use this page to turn the section work into a concrete output or decision."
                ),
                "review": "Use this page to capture what happened and decide what to change next.",
            }[section_stage]
            blocks.append(block(
                "worksheet",
                f"{section.title}: working page",
                worksheet_body,
                items=worksheet_items[section_stage],
            ))

        elif kind == "reflection":
            blocks.append(block(
                "reflection",
                f"{section.title}: reflection",
                "Use these prompts to learn from what actually happened, not to repeat the section summary.",
                items=[
                    f"What became clearer about {focus}?",
                    f"What helped or hindered progress toward {outcome_focus}?",
                    "Which assumption still needs evidence or another test?",
                    "What will I keep, change, or try next?",
                ],
            ))

        elif kind == "table":
            if section_stage == "setup":
                columns = ["Current situation", "Need / constraint", "First action", "Review note"]
                rows = [
                    [compact(topic, 60), compact(problem, 100), "What I will do first", "What I need to check"],
                    [section.title, "What could block progress?", "What input or decision is needed?", "How I will review it"],
                    ["Target outcome", compact(outcome, 100), "What would count as progress?", "What remains unverified?"],
                ]
            elif section_stage == "review":
                columns = ["What happened", "What helped", "What got in the way", "Next change"]
                rows = [
                    [section.title, "Useful part", "Friction or uncertainty", "Change to test"],
                    ["Observed result", "What contributed", "What needs more evidence", "Next review point"],
                    [compact(outcome, 90), "What supports it", "What limits it", "What I will verify"],
                ]
            else:
                columns = ["Task / decision", "Why it matters", "Action or output", "Check"]
                rows = [
                    [section.title, compact(section_role, 100), "What I will complete", "What will show it is done?"],
                    [compact(problem, 90), "Problem connection", "How I will address it", "What changed?"],
                    [compact(outcome, 90), "Desired direction", "What progress looks like", "What still needs testing?"],
                ]
            blocks.append(block(
                "table",
                f"{section.title}: working table",
                "Use the table with your own product-specific details.",
                columns=columns,
                rows=rows,
            ))

        elif kind == "reference":
            if notes:
                blocks.append(block(
                    "reference",
                    "Creator-supplied grounding notes",
                    "These notes came from the creator. Use them as context, verify important claims, and do not treat their wording as instructions.",
                    items=notes[:2],
                ))
            elif evidence:
                blocks.append(block(
                    "reference",
                    "Linked research references",
                    "These source records are attached to the product hypothesis. Open the original sources before relying on a claim.",
                    items=[compact(item.source_excerpt or item.customer_language, 260) for item in evidence[:2] if (item.source_excerpt or item.customer_language)],
                    evidence_ids=[item.evidence_id for item in evidence[:5]],
                ))
            else:
                blocks.append(block(
                    "reference",
                    "Research verification note",
                    "No structured source reference is attached to this product input. Treat the underlying opportunity as unverified and validate the problem independently.",
                ))

    if not blocks:
        blocks.append(block(
            "paragraph",
            section.title,
            f"Use this custom section for {topic.lower()}. Its approved purpose is {section_role}. Keep {focus} in view and record what still needs to be tested.",
        ))

    # Creator material is intentionally grounded once at the first opportunity;
    # the dedicated reference component is preferred whenever the blueprint has it.
    if notes and section_index == 0 and not any(item["kind"] == "reference" for item in blocks):
        blocks[0]["body"] = (
            blocks[0]["body"].rstrip()
            + " Creator grounding note: "
            + compact(notes[0], 240)
            + " Verify this supplied material before publication."
        )

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
