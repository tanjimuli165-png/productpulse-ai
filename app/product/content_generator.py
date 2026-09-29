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



def _local_section_content(blueprint: ProductBlueprint, section_index: int, evidence: list[EvidenceReference]) -> GeneratedSection:
    """Deterministic no-API-key content so the free deployment remains usable."""
    section = blueprint.outline[section_index]
    blocks = [
        {"kind": "paragraph", "title": section.title, "body": section.purpose, "items": [], "columns": [], "rows": [], "evidence_ids": []},
        {"kind": "steps", "title": "Work through this section", "body": "Use these steps with the reader's own situation. Examples are illustrative, not promised outcomes.", "items": [
            "Write down the current situation and the specific friction you want to improve.",
            "Choose one small change from this section and apply it to a real example.",
            "Record what changed, what remained difficult, and what should be tested next.",
        ], "columns": [], "rows": [], "evidence_ids": []},
        {"kind": "worksheet", "title": "Apply it", "body": "Complete the prompts before moving to the next section.", "items": [
            "My current situation:",
            "The specific step I will try:",
            "What I need to prepare:",
            "What I learned after trying it:",
        ], "columns": [], "rows": [], "evidence_ids": []},
    ]
    if evidence:
        blocks.append({"kind": "reference", "title": "Research references", "body": "These are the supplied research records linked to this product hypothesis. Validate the original sources before publication.", "items": [], "columns": [], "rows": [], "evidence_ids":[e.evidence_id for e in evidence[:5]]})
    return GeneratedSection(section_index=section_index, title=section.title, purpose=section.purpose, blocks=blocks)

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
            return _local_section_content(blueprint, section_index, references)
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
