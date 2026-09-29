from __future__ import annotations

from app.product.product_schema import GeneratedSection, ProductContent


def replace_content_section(content: ProductContent, replacement: GeneratedSection) -> ProductContent:
    """Return a validated content snapshot with only one section replaced or inserted.

    Other section payloads, the approved-blueprint fingerprint, and editable cover
    text remain unchanged. This supports safe section-by-section regeneration.
    """
    sections = {section.section_index: section for section in content.sections}
    sections[replacement.section_index] = replacement
    return ProductContent.model_validate({
        **content.model_dump(mode="json"),
        "sections": [section.model_dump(mode="json") for _, section in sorted(sections.items())],
    })


def replace_product_cover(
    content: ProductContent,
    *,
    title: str,
    subtitle: str,
) -> ProductContent:
    """Return a validated content snapshot with editable cover copy."""
    return ProductContent.model_validate({
        **content.model_dump(mode="json"),
        "product_title": title,
        "subtitle": subtitle,
    })


def replace_content_section_edits(
    content: ProductContent,
    *,
    section_index: int,
    title: str,
    purpose: str,
    blocks: list[dict],
) -> ProductContent:
    """Save text/structure edits for one existing section only."""
    if section_index not in {section.section_index for section in content.sections}:
        raise ValueError("The section to edit is not present in this saved product content.")
    sections = {section.section_index: section for section in content.sections}
    sections[section_index] = GeneratedSection(
        section_index=section_index,
        title=title,
        purpose=purpose,
        blocks=blocks,
    )
    return ProductContent.model_validate({
        **content.model_dump(mode="json"),
        "sections": [section.model_dump(mode="json") for _, section in sorted(sections.items())],
    })
