from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from pydantic import ValidationError

import streamlit as st
import streamlit.components.v1 as components

from app.database.models import Opportunity, Report
from app.evidence_metrics import is_eligible_evidence
from app.product.content_generator import ContentGenerationError, generate_section_content
from app.product.editing import replace_content_section, replace_content_section_edits, replace_product_cover
from app.product.pdf_builder import ProductPdfExportError, export_saved_product_pdf
from app.product.product_schema import DESIGN_TEMPLATE_IDS, PAGE_SIZE_OPTIONS, PRODUCT_TYPES, EvidenceReference, ProductBlueprint, ProductContent, ProductDesign, ProductInputs
from app.product.qa import final_verification, qa_snapshot_fingerprint, run_product_qa
from app.product.storage import ProductStore
from app.product.strategy import BlueprintGenerationError, clean_product_inputs, generate_blueprint, recommend_product_types
from app.product.template_engine import PAGE_SIZES, design_details, get_template, render_product_html, template_recommendation
logger = logging.getLogger(__name__)


from app.product.visual_generator import (
    ACCENT_COLORS,
    ICON_OPTIONS,
    SHAPE_OPTIONS,
    VisualAssetData,
    VisualGenerationError,
    create_visual_asset,
    sanitize_uploaded_image,
    suggest_visual_ideas,
)


def _matching_problem(report: Report, opportunity: Opportunity):
    if report.scoring_version == "manual_topic_v1" and report.problems:
        return report.problems[0]
    audience = (opportunity.audience or "").lower()
    return next((problem for problem in report.problems if problem.problem.lower() in audience), None)


def build_product_inputs(report: Report, opportunity: Opportunity) -> ProductInputs:
    """Map one selected opportunity to a compact, source-linked builder input."""
    problem = _matching_problem(report, opportunity)
    evidence_refs: list[EvidenceReference] = []
    if problem:
        if report.scoring_version == "manual_topic_v1":
            source_items = report.evidence[:10]
        else:
            urls = set(problem.evidence_urls)
            source_items = [item for item in report.evidence if item.url and item.url in urls and is_eligible_evidence(item)][:10]
        for item in source_items:
            identity = f"{item.source}\n{item.url}\n{item.title}\n{item.text[:500]}".encode("utf-8")
            evidence_refs.append(EvidenceReference(
                evidence_id="EV-" + hashlib.sha256(identity).hexdigest()[:12].upper(),
                source_title=item.title or "Untitled source",
                source_type=item.source,
                customer_language=problem.problem,
                url=item.url,
            ))
    return ProductInputs(
        product_title=opportunity.name,
        audience=opportunity.audience,
        problem=problem.problem if problem else opportunity.promise,
        promise=opportunity.promise,
        format_hints=opportunity.format,
        differentiation=opportunity.differentiation,
        evidence=evidence_refs,
        validation_steps=opportunity.next_steps,
    )


def _list_text(values: list[str]) -> str:
    return "\n".join(values)


def _outline_text(blueprint: ProductBlueprint) -> str:
    return "\n".join(
        f"{section.title} | {section.purpose} | {'; '.join(section.components)}"
        for section in blueprint.outline
    )


def _parse_outline(text: str) -> list[dict[str, Any]]:
    sections = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        title = parts[0]
        purpose = parts[1] if len(parts) > 1 and parts[1] else "Section purpose to be refined."
        components = [part.strip() for part in parts[2].replace(",", ";").split(";") if part.strip()] if len(parts) > 2 else []
        sections.append({"title": title, "purpose": purpose, "components": components})
    return sections


def _line_items(text: str) -> list[str]:
    return [line.strip().lstrip("-• ").strip() for line in text.splitlines() if line.strip()]


def _page_summary(blueprint: ProductBlueprint) -> None:
    st.subheader("Product blueprint")
    st.info("Research-backed product concept — an evidence-supported hypothesis worth validating, not a guarantee of demand or sales.")
    st.markdown(f"### {blueprint.title}\n*{blueprint.subtitle}*")
    summary_cols = st.columns(2)
    summary_cols[0].write(f"**Target audience**\n\n{blueprint.target_audience}")
    summary_cols[1].write(f"**Selected type**\n\n{blueprint.product_type} · estimated {blueprint.estimated_page_count} pages")
    st.write(f"**Core problem:** {blueprint.core_problem}")
    st.write(f"**Desired outcome:** {blueprint.desired_outcome}")
    st.write(f"**Product promise:** {blueprint.promise}")
    st.markdown("**Type recommendation**")
    st.write(", ".join(blueprint.recommended_types))
    st.caption(blueprint.recommendation_reason)
    st.markdown("**Structure**")
    for section in blueprint.outline:
        with st.expander(section.title):
            st.write(section.purpose)
            if section.components:
                st.write("**Planned components:** " + "; ".join(section.components))
    component_groups = [
        ("Exercises", blueprint.exercises),
        ("Checklists", blueprint.checklists),
        ("Worksheets", blueprint.worksheets),
        ("Examples", blueprint.examples),
        ("Templates", blueprint.templates),
        ("Bonuses", blueprint.bonuses),
    ]
    for label, values in component_groups:
        if values:
            st.markdown(f"**{label}**")
            for value in values:
                st.write(f"- {value}")
    st.markdown("**Design direction**")
    st.write(blueprint.design_direction)


def _blueprint_from_editor(blueprint: ProductBlueprint, prefix: str) -> ProductBlueprint:
    values = blueprint.model_dump(mode="json")
    values.update({
        "title": st.text_input("Product title", values["title"], key=f"{prefix}_edit_title", max_chars=160),
        "subtitle": st.text_input("Subtitle", values["subtitle"], key=f"{prefix}_edit_subtitle", max_chars=260),
        "target_audience": st.text_area("Target audience", values["target_audience"], key=f"{prefix}_edit_audience", max_chars=600),
        "core_problem": st.text_area("Core problem", values["core_problem"], key=f"{prefix}_edit_problem", max_chars=1200),
        "desired_outcome": st.text_area("Desired outcome", values["desired_outcome"], key=f"{prefix}_edit_outcome", max_chars=600),
        "promise": st.text_area("Product promise", values["promise"], key=f"{prefix}_edit_promise", max_chars=900),
        "product_type": st.selectbox("Product type", PRODUCT_TYPES, index=PRODUCT_TYPES.index(values["product_type"]), key=f"{prefix}_edit_type"),
        "estimated_page_count": int(st.number_input("Estimated page count", min_value=3, max_value=120, value=values["estimated_page_count"], key=f"{prefix}_edit_pages")),
        "outline": _parse_outline(st.text_area("Outline — one section per line: Title | purpose | components separated by semicolons", _outline_text(blueprint), key=f"{prefix}_edit_outline", height=230)),
        "exercises": _line_items(st.text_area("Exercises — one item per line", _list_text(values["exercises"]), key=f"{prefix}_edit_exercises", height=100)),
        "checklists": _line_items(st.text_area("Checklists — one item per line", _list_text(values["checklists"]), key=f"{prefix}_edit_checklists", height=100)),
        "worksheets": _line_items(st.text_area("Worksheets — one item per line", _list_text(values["worksheets"]), key=f"{prefix}_edit_worksheets", height=100)),
        "examples": _line_items(st.text_area("Examples — one item per line", _list_text(values["examples"]), key=f"{prefix}_edit_examples", height=100)),
        "templates": _line_items(st.text_area("Templates — one item per line", _list_text(values["templates"]), key=f"{prefix}_edit_templates", height=100)),
        "bonuses": _line_items(st.text_area("Bonuses — one item per line", _list_text(values["bonuses"]), key=f"{prefix}_edit_bonuses", height=100)),
        "design_direction": st.text_area("Design direction", values["design_direction"], key=f"{prefix}_edit_design", max_chars=800),
    })
    return ProductBlueprint.model_validate(values)


def _edit_inputs(inputs: ProductInputs, prefix: str) -> ProductInputs:
    values = inputs.model_dump(mode="json")
    with st.expander("Review / edit research-derived product inputs", expanded=not bool(st.session_state.get(f"{prefix}_inputs_applied"))):
        with st.form(f"{prefix}_input_form"):
            title = st.text_input("Working product title", values["product_title"], key=f"{prefix}_input_title", max_chars=160)
            audience = st.text_area("Audience", values["audience"], key=f"{prefix}_input_audience", max_chars=600)
            problem = st.text_area("Problem", values["problem"], key=f"{prefix}_input_problem", max_chars=1200)
            promise = st.text_area("Opportunity promise", values["promise"], key=f"{prefix}_input_promise", max_chars=900)
            formats = st.text_input("Format hints (comma-separated)", ", ".join(values["format_hints"]), key=f"{prefix}_input_formats")
            differentiation = st.text_area("Differentiation (one item per line)", _list_text(values["differentiation"]), key=f"{prefix}_input_diff", height=100)
            validation = st.text_area("Validation steps (one per line)", _list_text(values["validation_steps"]), key=f"{prefix}_input_validation", height=100)
            reference_material = st.text_area(
                "Related notes / source material",
                values.get("reference_material", ""),
                key=f"{prefix}_input_reference_material",
                height=160,
                max_chars=50000,
                help="Keep your own notes, facts, examples, outline, or supplied source material here. This material is passed to content generation as creator-provided context.",
            )
            applied = st.form_submit_button("Apply input edits")
        if applied:
            try:
                updated = ProductInputs(
                    product_title=title,
                    audience=audience,
                    problem=problem,
                    promise=promise,
                    format_hints=[item.strip() for item in formats.split(",") if item.strip()],
                    differentiation=_line_items(differentiation),
                    evidence=values["evidence"],
                    validation_steps=_line_items(validation),
                    reference_material=reference_material,
                )
                st.session_state[f"{prefix}_current_inputs"] = updated.model_dump(mode="json")
                st.session_state[f"{prefix}_inputs_applied"] = True
                st.success("Product inputs updated. Source references remain linked to the selected research records.")
                st.rerun()
            except Exception as exc:
                st.error(f"Please correct the product inputs: {exc}")
    return ProductInputs.model_validate(st.session_state.get(f"{prefix}_current_inputs", values))


def _blueprint_fingerprint(blueprint: ProductBlueprint) -> str:
    serialized = json.dumps(blueprint.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _render_generated_content(content: ProductContent, evidence: list[EvidenceReference]) -> None:
    reference_map = {item.evidence_id: item for item in evidence}
    for section in sorted(content.sections, key=lambda item: item.section_index):
        with st.expander(f"{section.section_index + 1}. {section.title}", expanded=False):
            st.caption(section.purpose)
            for block in section.blocks:
                if block.title:
                    st.markdown(f"**{block.title}**")
                if block.body:
                    st.write(block.body)
                if block.kind == "table":
                    st.table([dict(zip(block.columns, row)) for row in block.rows])
                elif block.items:
                    if block.kind == "checklist":
                        for item in block.items:
                            st.write(f"☐ {item}")
                    else:
                        st.write(block.items)
                for evidence_id in block.evidence_ids:
                    reference = reference_map.get(evidence_id)
                    if reference:
                        label = f"{reference.source_type}: {reference.source_title} · {reference.evidence_id}"
                        st.markdown(f"- [{label}]({reference.url})" if reference.url else f"- {label}")


def _table_editor_rows(text: str, column_count: int) -> list[list[str]]:
    """Parse editable tab-separated table rows and keep the shape explicit."""
    rows = [[cell.strip() for cell in line.split("\t")] for line in text.splitlines() if line.strip()]
    if not rows or any(len(row) != column_count for row in rows):
        raise ValueError("Enter at least one table row, with cells separated by tabs to match the column count.")
    if len(rows) > 20 or any(len(cell) > 500 for row in rows for cell in row):
        raise ValueError("Tables are limited to 20 rows and 500 characters per cell.")
    return rows


def _render_content_editor(
    product_store: ProductStore,
    user_id: str,
    product_id: str,
    content: ProductContent,
    blueprint: ProductBlueprint,
    evidence: list[EvidenceReference],
    reference_material: str = "",
) -> None:
    """Provide compact, structured text editing and one-section regeneration."""
    st.divider()
    st.subheader("Edit product content · Phase 6")
    st.caption(
        "Edit cover copy, section names, purposes, examples, paragraphs, lists, and table cells. "
        "Each save creates a private content version; section regeneration replaces only that section."
    )
    with st.form(f"product_{product_id}_cover_copy_form"):
        title = st.text_input("Product title", content.product_title, max_chars=160, key=f"product_{product_id}_content_title")
        subtitle = st.text_input(
            "Subtitle", content.subtitle or blueprint.subtitle, max_chars=260,
            key=f"product_{product_id}_content_subtitle",
        )
        cover_saved = st.form_submit_button("Save cover copy")
    if cover_saved:
        try:
            updated = replace_product_cover(content, title=title, subtitle=subtitle)
            product_store.save_content(
                product_id=product_id, user_id=user_id,
                content_payload=updated.model_dump(mode="json"),
                change_summary="Edited product title and subtitle",
            )
            st.success("Cover copy saved as a new content version.")
            st.rerun()
        except Exception as exc:
            st.error(f"Cover copy was not saved ({type(exc).__name__}). Correct the text and retry; the previous version remains intact.")

    for section in sorted(content.sections, key=lambda item: item.section_index):
        with st.expander(f"Edit section {section.section_index + 1}: {section.title}", expanded=False):
            st.caption("Regeneration uses the approved outline and linked evidence, and replaces this section only.")
            with st.form(f"product_{product_id}_section_edit_{section.section_index}"):
                title_value = st.text_input(
                    "Section name", section.title, max_chars=120,
                    key=f"product_{product_id}_section_{section.section_index}_title",
                )
                purpose_value = st.text_area(
                    "Section purpose / introduction", section.purpose, max_chars=500,
                    key=f"product_{product_id}_section_{section.section_index}_purpose",
                )
                edited_blocks: list[dict] = []
                table_parse_error = None
                for block_index, block in enumerate(section.blocks):
                    values = block.model_dump(mode="json")
                    st.markdown(f"**Block {block_index + 1} · {block.kind.replace('_', ' ').title()}**")
                    values["title"] = st.text_input(
                        "Block heading", values["title"], max_chars=120,
                        key=f"product_{product_id}_section_{section.section_index}_block_{block_index}_title",
                    )
                    values["body"] = st.text_area(
                        "Text / example", values["body"], max_chars=5000,
                        key=f"product_{product_id}_section_{section.section_index}_block_{block_index}_body",
                    )
                    if values["items"]:
                        values["items"] = _line_items(st.text_area(
                            "List items — one per line", "\n".join(values["items"]), height=110,
                            key=f"product_{product_id}_section_{section.section_index}_block_{block_index}_items",
                        ))
                    if values["kind"] == "table":
                        columns_text = st.text_input(
                            "Table columns — tab-separated", "\t".join(values["columns"]),
                            key=f"product_{product_id}_section_{section.section_index}_block_{block_index}_columns",
                        )
                        values["columns"] = [cell.strip() for cell in columns_text.split("\t")]
                        rows_text = "\n".join("\t".join(row) for row in values["rows"])
                        edited_rows_text = st.text_area(
                            "Table rows — one row per line, cells separated by tabs", rows_text, height=130,
                            key=f"product_{product_id}_section_{section.section_index}_block_{block_index}_rows",
                        )
                        try:
                            values["rows"] = _table_editor_rows(edited_rows_text, len(values["columns"]))
                        except ValueError as exc:
                            table_parse_error = str(exc)
                            values["columns"] = block.columns
                            values["rows"] = block.rows
                    edited_blocks.append(values)
                edit_saved = st.form_submit_button("Save section edits")
            if edit_saved:
                if table_parse_error:
                    st.error(table_parse_error)
                else:
                    try:
                        updated = replace_content_section_edits(
                            content, section_index=section.section_index, title=title_value,
                            purpose=purpose_value, blocks=edited_blocks,
                        )
                        product_store.save_content(
                            product_id=product_id, user_id=user_id,
                            content_payload=updated.model_dump(mode="json"),
                            change_summary=f"Edited section {section.section_index + 1}: {title_value.strip()}",
                        )
                        st.success("Section edits saved as a new content version.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Section edits were not saved ({type(exc).__name__}). Check required text/table values and retry; the previous version remains intact.")
            if st.button(
                f"Regenerate section {section.section_index + 1}",
                key=f"product_{product_id}_section_regenerate_{section.section_index}",
                help="Replaces only this section with newly generated content; other saved sections remain unchanged.",
            ):
                try:
                    generated = generate_section_content(blueprint, section.section_index, evidence, reference_material)
                    updated = replace_content_section(content, generated)
                    product_store.save_content(
                        product_id=product_id, user_id=user_id,
                        content_payload=updated.model_dump(mode="json"),
                        change_summary=f"Regenerated section {section.section_index + 1}: {generated.title}",
                    )
                    st.success(f"Section {section.section_index + 1} regenerated; other sections were preserved.")
                    st.rerun()
                except ContentGenerationError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"The section was not regenerated ({type(exc).__name__}). Existing sections and edits remain saved; retry after checking provider settings.")


def _render_visual_engine(
    product_store: ProductStore,
    user_id: str,
    product_id: str,
    blueprint: ProductBlueprint,
    content: ProductContent,
) -> None:
    """Create and manage bounded, account-scoped visual assets for Phase 5."""
    st.subheader("Content visuals · Phase 5")
    st.caption(
        "Create reusable icons, simple shapes, and accurate step-flow diagrams, or upload a PNG/JPEG. "
        "Generated visuals are deterministic; AI image generation is intentionally deferred by the Master Plan."
    )
    locations = {"cover": "Cover"}
    locations.update({f"section:{section.section_index}": f"Section {section.section_index + 1}: {section.title}" for section in content.sections})

    st.markdown("**Suggested visuals for this product**")
    st.caption("Use a suggestion to prefill the visual creator. Nothing is created or saved until you press Create and save visual.")
    ideas = suggest_visual_ideas(
        blueprint.product_type,
        blueprint.core_problem or blueprint.title,
        [section.model_dump(mode="json") for section in blueprint.outline],
    )
    for index, idea in enumerate(ideas[:3]):
        cols = st.columns([5, 1])
        with cols[0]:
            st.write(f"**{idea['title']}** · {idea['placement']}")
            st.caption(idea["reason"])
        if cols[1].button("Use idea", key=f"product_{product_id}_visual_idea_{index}"):
            placement = "cover" if idea["placement"].lower() == "cover" else next(
                (f"section:{section.section_index}" for section in content.sections if section.title == idea["placement"]),
                "cover",
            )
            title_lower = idea["title"].lower()
            visual_kind = "diagram" if any(word in title_lower for word in ("map", "flow", "roadmap", "loop", "overview", "anatomy")) else "icon"
            st.session_state[f"product_{product_id}_visual_type"] = visual_kind
            st.session_state[f"product_{product_id}_visual_title"] = idea["title"]
            st.session_state[f"product_{product_id}_visual_placement"] = placement
            if visual_kind == "diagram":
                matched_section = next(
                    (section for section in content.sections if section.title == idea["placement"]),
                    None,
                )
                component_labels = {
                    "paragraph": "Understand",
                    "steps": "Follow steps",
                    "action_steps": "Take action",
                    "example": "Review example",
                    "exercise": "Practice",
                    "worksheet": "Write",
                    "reflection": "Reflect",
                    "table": "Track",
                    "checklist": "Check",
                    "reference": "Review source",
                }
                derived_steps = []
                if matched_section is not None:
                    for component in matched_section.components:
                        label = component_labels.get(str(component).strip().lower())
                        if label and label not in derived_steps:
                            derived_steps.append(label)
                derived_steps = derived_steps[:5]
                if len(derived_steps) < 2:
                    derived_steps = ["Start", "Work", "Review"]
                st.session_state[f"product_{product_id}_visual_steps"] = "\n".join(derived_steps)
            else:
                st.session_state[f"product_{product_id}_visual_steps"] = ""
            st.session_state[f"product_{product_id}_visual_icon"] = "target" if any(word in title_lower for word in ("goal", "priority", "progress", "roadmap")) else "check"
            st.rerun()

    create_tab, upload_tab, saved_tab = st.tabs(["Create a visual", "Upload an image", "Saved visuals"])
    with create_tab:
        visual_kind = st.selectbox(
            "Visual type", ["icon", "shape", "diagram"],
            format_func=lambda value: {"icon": "Simple icon", "shape": "Simple shape", "diagram": "Process diagram"}[value],
            key=f"product_{product_id}_visual_type",
        )
        with st.form(f"product_{product_id}_visual_create_form"):
            title = st.text_input("Visual title", max_chars=120, key=f"product_{product_id}_visual_title")
            placement = st.selectbox(
                "Place visual", list(locations), format_func=lambda value: locations[value],
                key=f"product_{product_id}_visual_placement",
            )
            palette = sorted(ACCENT_COLORS)
            current_design = st.session_state.get(f"product_{product_id}_design_template", "minimal_professional")
            suggested_accent = get_template(current_design).palette[1] if current_design in DESIGN_TEMPLATE_IDS else "#2B756F"
            accent = st.selectbox(
                "Accent color", palette, index=palette.index(suggested_accent),
                key=f"product_{product_id}_visual_accent",
            )
            icon = "check"
            shape = "highlight_card"
            steps: list[str] = []
            if visual_kind == "icon":
                icon = st.selectbox("Icon", list(ICON_OPTIONS.values()), format_func=lambda value: next(label for label, item in ICON_OPTIONS.items() if item == value), key=f"product_{product_id}_visual_icon")
            elif visual_kind == "shape":
                shape = st.selectbox("Shape", list(SHAPE_OPTIONS.values()), format_func=lambda value: next(label for label, item in SHAPE_OPTIONS.items() if item == value), key=f"product_{product_id}_visual_shape")
            else:
                steps = [line.strip() for line in st.text_area("Process steps — one per line, 2–5 steps", key=f"product_{product_id}_visual_steps", height=120).splitlines() if line.strip()]
            submitted = st.form_submit_button("Create and save visual", type="primary")
        if submitted:
            try:
                visual = create_visual_asset(visual_kind, title, icon=icon, shape=shape, steps=steps, accent_color=accent)
                product_store.save_visual_asset(
                    product_id=product_id, user_id=user_id, asset_type=visual.asset_type,
                    title=visual.title, filename=visual.filename, mime_type=visual.mime_type,
                    placement=placement, metadata=visual.metadata, content=visual.content,
                )
                st.success("Visual created and saved to this product.")
                st.rerun()
            except VisualGenerationError as exc:
                st.error(str(exc))
            except (ValueError, LookupError, PermissionError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"The visual was not saved ({type(exc).__name__}). Existing product visuals and content remain unchanged; retry after checking the product draft.")

    with upload_tab:
        st.caption("PNG and JPEG only · 2 MB maximum · uploaded images are decoded and re-encoded before saving.")
        with st.form(f"product_{product_id}_visual_upload_form"):
            uploaded = st.file_uploader("Choose a PNG or JPEG image", type=["png", "jpg", "jpeg"], key=f"product_{product_id}_visual_upload")
            upload_title = st.text_input("Optional image label", max_chars=120, key=f"product_{product_id}_visual_upload_title")
            upload_placement = st.selectbox(
                "Place uploaded image", list(locations), format_func=lambda value: locations[value],
                key=f"product_{product_id}_visual_upload_placement",
            )
            upload_submitted = st.form_submit_button("Validate and save image")
        if upload_submitted:
            try:
                if uploaded is None:
                    raise VisualGenerationError("Choose a PNG or JPEG image to upload.")
                visual = sanitize_uploaded_image(uploaded.getvalue(), uploaded.name)
                if upload_title.strip():
                    visual = VisualAssetData(
                        asset_type=visual.asset_type, title=upload_title.strip(), mime_type=visual.mime_type,
                        filename=visual.filename, content=visual.content, metadata=visual.metadata,
                    )
                product_store.save_visual_asset(
                    product_id=product_id, user_id=user_id, asset_type=visual.asset_type,
                    title=visual.title, filename=visual.filename, mime_type=visual.mime_type,
                    placement=upload_placement, metadata=visual.metadata, content=visual.content,
                )
                st.success("Image validated, cleaned, and saved to this product.")
                st.rerun()
            except VisualGenerationError as exc:
                st.error(str(exc))
            except (ValueError, LookupError, PermissionError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"The image was not saved ({type(exc).__name__}). Existing visuals and content remain unchanged; retry with a supported image.")

    with saved_tab:
        assets = product_store.list_visual_assets(product_id, user_id)
        if not assets:
            st.info("No visuals have been saved for this product yet.")
        else:
            st.caption(f"{len(assets)} of 12 available visual slots used. Assets are private to this account and product.")
            for asset in assets:
                columns = st.columns([5, 1])
                where = locations.get(asset["placement"], "Saved placement")
                columns[0].write(f"**{asset['title']}** · {asset['asset_type']} · {where}")
                columns[0].caption(asset["filename"])
                if columns[1].button("Remove", key=f"product_{product_id}_visual_remove_{asset['asset_id']}"):
                    try:
                        if product_store.delete_visual_asset(product_id=product_id, user_id=user_id, asset_id=asset["asset_id"]):
                            st.success("Visual removed from this product.")
                            st.rerun()
                        st.warning("The visual was not found in this product; refresh the page and try again.")
                    except Exception as exc:
                        st.error(f"The visual could not be removed ({type(exc).__name__}).")


def _render_template_engine(
    product_store: ProductStore,
    user_id: str,
    product_id: str,
    content: ProductContent,
    blueprint: ProductBlueprint,
    evidence: list[EvidenceReference],
    saved_product: dict,
) -> None:
    """Select an account-scoped template and render the same content in its layout."""
    st.divider()
    st.subheader("Design templates · Phase 4")
    recommended_id, reason = template_recommendation(blueprint.product_type)
    st.caption(f"Suggested starting point: {get_template(recommended_id).name}. {reason} You can choose any template.")
    current_template = saved_product.get("design_template_id", "minimal_professional")
    current_size = saved_product.get("page_size", "letter")
    if current_template not in DESIGN_TEMPLATE_IDS:
        current_template = recommended_id
    if current_size not in PAGE_SIZE_OPTIONS:
        current_size = "letter"
    with st.form(f"product_{product_id}_design_form"):
        template_id = st.selectbox(
            "Page design",
            DESIGN_TEMPLATE_IDS,
            index=DESIGN_TEMPLATE_IDS.index(current_template),
            format_func=lambda value: get_template(value).name,
            key=f"product_{product_id}_design_template",
        )
        page_size = st.selectbox(
            "Page size",
            PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(current_size),
            format_func=lambda value: PAGE_SIZES[value]["label"],
            key=f"product_{product_id}_design_page_size",
        )
        applied = st.form_submit_button("Apply and save design")
    if applied:
        try:
            saved_product = product_store.save_design(
                product_id=product_id,
                user_id=user_id,
                design=ProductDesign(template_id=template_id, page_size=page_size),
            )
            st.success(f"{get_template(template_id).name} saved to this product draft.")
            st.rerun()
        except Exception as exc:
            st.error(f"The design choice was not saved ({type(exc).__name__}). Your existing content and saved design remain unchanged.")
            return
    active_template = saved_product.get("design_template_id", "minimal_professional")
    active_size = saved_product.get("page_size", "letter")
    details = design_details(active_template)
    st.markdown(f"**Applied template:** {details['name']} · {PAGE_SIZES[active_size]['label']}")
    st.caption(str(details["description"]))
    palette = details.get("palette", ())
    if isinstance(palette, (tuple, list)) and len(palette) >= 3:
        swatches = "".join(
            f'<span title="{escape}" style="display:inline-block;width:28px;height:28px;border-radius:8px;'
            f'background:{color};border:1px solid rgba(15,23,42,.12);margin-right:7px;vertical-align:middle"></span>'
            for color, escape in zip(palette[:3], palette[:3])
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:4px;margin:.45rem 0 .75rem">'
            f'<span style="font-size:.78rem;font-weight:700;color:#475569;margin-right:6px">Brand palette</span>{swatches}</div>',
            unsafe_allow_html=True,
        )
    detail_cols = st.columns(2)
    detail_cols[0].write("**Best suited to:** " + ", ".join(details["best_for"]))
    detail_cols[1].write("**Type layouts:** " + ", ".join(dict(get_template(active_template).layouts).values()))
    st.caption(
        f"Typography: {details['typography'][0]} / {details['typography'][1]} · "
        f"Accent: {details['accent_treatment']} · Footer: {details['footer_treatment']}"
    )
    st.caption("This is an editable, indicative in-app preview. Phase 7 checks saved content and this preview; final PDF pagination and physical overflow still require manual review after export.")
    _render_visual_engine(product_store, user_id, product_id, blueprint, content)
    visual_assets = product_store.get_visual_assets(product_id, user_id)
    html = render_product_html(
        content,
        active_template,
        active_size,
        subtitle=content.subtitle,
        product_type=blueprint.product_type,
        audience=blueprint.target_audience,
        evidence=evidence,
        visual_assets=visual_assets,
    )
    st.divider()
    st.subheader("Live product preview")
    st.caption("The preview below reflects the saved cover and section edits, selected template/page size, and saved visuals.")
    height = min(4200, max(900, 780 * (len(content.sections) + 1)))
    components.html(html, height=height, scrolling=True)


def _render_quality_assurance(
    product_store: ProductStore,
    user_id: str,
    product_id: str,
    content: ProductContent,
    blueprint: ProductBlueprint,
    inputs: ProductInputs,
    saved_product: dict,
) -> None:
    """Run and show deterministic QA for this owner's current saved content and preview."""
    st.divider()
    st.subheader("Automated content & preview QA · Phase 7")
    st.caption(
        "Checks are deterministic heuristics and structure comparisons, not an arbitrary quality score. "
        "A PASS means only that these available checks raised no finding; it is not a guarantee of accuracy, usefulness, safety, demand, or sales."
    )
    if saved_product.get("status") != "approved":
        st.info("Approve the blueprint and save its content before running QA.")
        return

    template_id = saved_product.get("design_template_id", "minimal_professional")
    page_size = saved_product.get("page_size", "letter")
    try:
        visual_assets = product_store.get_visual_assets(product_id, user_id)
        preview_html = render_product_html(
            content,
            template_id,
            page_size,
            subtitle=content.subtitle,
            product_type=blueprint.product_type,
            audience=blueprint.target_audience,
            evidence=inputs.evidence,
            visual_assets=visual_assets,
        )
        design = {"template_id": template_id, "page_size": page_size}
        fingerprint = qa_snapshot_fingerprint(
            blueprint, content, design=design, visual_assets=visual_assets, preview_html=preview_html
        )
    except Exception as exc:
        st.error(f"QA could not prepare the current saved preview ({type(exc).__name__}). No result was saved; review the saved design and visuals, then retry.")
        return

    if st.button("Run automated QA", type="primary", key=f"product_{product_id}_run_qa"):
        try:
            result = run_product_qa(
                blueprint,
                content,
                inputs,
                design=design,
                visual_assets=visual_assets,
                preview_html=preview_html,
            )
            product_store.save_qa_run(
                product_id=product_id,
                user_id=user_id,
                snapshot_fingerprint=fingerprint,
                result_payload=result,
            )
            st.success("QA result saved to this product's private history.")
        except PermissionError:
            st.error("This QA run could not be saved because the product belongs to another account.")
        except (LookupError, ValueError) as exc:
            st.error(f"QA result was not saved: {exc}")
        except Exception as exc:
            st.error(f"QA could not complete or save ({type(exc).__name__}). Existing content and earlier QA results remain unchanged; retry after checking the saved product.")

    try:
        runs = product_store.list_qa_runs(product_id, user_id, limit=10)
    except Exception as exc:
        st.error(f"Saved QA history could not be loaded ({type(exc).__name__}); the current product content is unchanged.")
        return
    if not runs:
        st.info("No QA run has been saved for this product yet.")
        return

    latest = runs[0]
    latest_result = latest["result_payload"]
    is_stale = latest["snapshot_fingerprint"] != fingerprint
    if is_stale:
        st.warning("The latest QA result is stale: saved content, blueprint, design, or visuals changed after that run. Run QA again before relying on it.")
    status = latest_result.get("status", "NEEDS REVISION")
    if status == "PASS" and not is_stale:
        st.success("PASS for checks available on the current saved content and preview — PDF rendering is not validated.")
    else:
        st.warning(f"{status}{' · result is stale' if is_stale else ''}. Review the findings below and rerun QA after changes.")
    st.caption(f"Saved {latest['created_at']} · checks use the saved snapshot. No scalar quality score is produced.")

    for check in latest_result.get("checks", []):
        label = f"{check.get('status', 'NOT RUN')} · {check.get('category', 'QA')} · {check.get('name', 'Check')}"
        with st.expander(label, expanded=check.get("status") == "FLAG" or check.get("status") == "NOT RUN"):
            st.write(check.get("message", "No check details."))
            if check.get("issues"):
                st.markdown("**Actionable findings**")
                for issue in check["issues"]:
                    st.write(f"- {issue}")
            st.caption("Method / limit: " + check.get("method", "Not specified."))
    with st.expander("What this QA does not establish"):
        for limitation in latest_result.get("limitations", []):
            st.write(f"- {limitation}")

    if len(runs) > 1:
        with st.expander("Earlier private QA runs"):
            for run in runs[1:]:
                result = run["result_payload"]
                is_older_stale = run["snapshot_fingerprint"] != fingerprint
                st.write(
                    f"**{result.get('status', 'Unknown')}** · {run['created_at']}"
                    f"{' · stale snapshot' if is_older_stale else ''}"
                )



def _render_final_verification(product_store: ProductStore, user_id: str, product_id: str, content: ProductContent, blueprint: ProductBlueprint, inputs: ProductInputs, saved_product: dict) -> None:
    """Run the last deterministic pre-publish gate against the saved product and exported PDF."""
    st.divider()
    st.subheader("Final verification · before download")
    st.caption("Last gate: content, repetition, preview structure, visuals, and the actual exported PDF are checked together.")
    st.markdown("""
    <div style="display:flex;gap:.45rem;flex-wrap:wrap;margin:.35rem 0 1rem">
      <span style="padding:.42rem .68rem;border:1px solid #B9D8D3;border-radius:999px;background:#E6F2F0;color:#134E4A;font-weight:750;font-size:.78rem">01 Save product</span>
      <span style="padding:.42rem .68rem;border:1px solid #B9D8D3;border-radius:999px;background:#E6F2F0;color:#134E4A;font-weight:750;font-size:.78rem">02 Automated QA</span>
      <span style="padding:.42rem .68rem;border:1px solid #B9D8D3;border-radius:999px;background:#E6F2F0;color:#134E4A;font-weight:750;font-size:.78rem">03 Final verification</span>
      <span style="padding:.42rem .68rem;border:1px solid #D8E2E0;border-radius:999px;background:#fff;color:#475569;font-weight:750;font-size:.78rem">04 Download</span>
    </div>
    """, unsafe_allow_html=True)
    template_id = saved_product.get("design_template_id", "minimal_professional")
    page_size = saved_product.get("page_size", "letter")
    try:
        visual_assets = product_store.get_visual_assets(product_id, user_id)
        preview_html = render_product_html(
            content, template_id, page_size, subtitle=content.subtitle,
            product_type=blueprint.product_type, audience=blueprint.target_audience,
            evidence=inputs.evidence, visual_assets=visual_assets,
        )
        fingerprint = qa_snapshot_fingerprint(
            blueprint, content, design={"template_id": template_id, "page_size": page_size},
            visual_assets=visual_assets, preview_html=preview_html,
        )
    except Exception as exc:
        st.error(f"Final verification could not prepare the current saved snapshot ({type(exc).__name__}).")
        return

    if st.button("Run final verification", type="primary", key=f"product_{product_id}_final_verify"):
        try:
            export = export_saved_product_pdf(product_id, user_id, product_store)
            result = final_verification(
                blueprint, content, inputs,
                design={"template_id": template_id, "page_size": page_size},
                visual_assets=visual_assets, preview_html=preview_html, pdf_bytes=export.pdf_bytes,
            )
            product_store.save_qa_run(
                product_id=product_id, user_id=user_id,
                snapshot_fingerprint=fingerprint, result_payload=result,
            )
            st.session_state[f"product_{product_id}_final_verified"] = result.get("status") == "PASS"
            if result.get("status") == "PASS":
                st.success("Final verification PASS — the current saved product and exported PDF passed all available deterministic checks.")
            else:
                st.warning("Final verification found items that need review. See the findings below.")
            st.rerun()
        except ProductPdfExportError as exc:
            st.error(f"Final verification could not export the current product: {exc}")
        except Exception as exc:
            st.error(f"Final verification failed ({type(exc).__name__}). Your saved product was not changed.")

    try:
        runs = product_store.list_qa_runs(product_id, user_id, limit=10)
    except Exception:
        runs = []
    final_runs = [r for r in runs if r.get("result_payload", {}).get("final_verification")]
    if final_runs:
        result = final_runs[0].get("result_payload", {})
        status = result.get("status", "NEEDS REVISION")
        checks = result.get("checks", [])
        flagged = sum(1 for check in checks if check.get("status") in {"FLAG", "NOT RUN"})
        if status == "PASS":
            st.success("FINAL VERIFICATION PASS")
            st.caption("The current saved snapshot passed the available final checks. Download remains locked if the saved snapshot changes.")
        else:
            st.warning("FINAL VERIFICATION NEEDS REVIEW")
            st.caption(f"{flagged} check(s) need attention. Fix the findings, save changes, rerun Automated QA, then run Final verification again.")
        for check in checks:
            if check.get("status") in {"FLAG", "NOT RUN"}:
                with st.expander(f"{check.get('status')} · {check.get('name')}", expanded=True):
                    st.write(check.get("message", ""))
                    for issue in check.get("issues", []):
                        st.write(f"- {issue}")

def _render_pdf_export(product_store: ProductStore, user_id: str, product_id: str) -> None:
    """Offer an in-memory PDF assembled only from the owner's saved product snapshot."""
    st.divider()
    st.subheader("Export final product PDF")
    st.caption(
        "The PDF is assembled from this account's approved, saved blueprint and complete saved content, "
        "using the saved template, page size, and allowed private visuals."
    )
    try:
        export = export_saved_product_pdf(product_id, user_id, product_store)
    except ProductPdfExportError as exc:
        st.info(f"PDF export is not ready: {exc}")
        return
    except Exception as exc:
        st.error(
            f"The product PDF could not be prepared ({type(exc).__name__}). "
            "The saved product was not changed; check the product data and try again."
        )
        return

    if export.qa_history_unavailable:
        st.warning("The latest private QA history could not be read for this export. The final PDF is locked until the saved QA history is verified and Final verification passes.")
    elif export.latest_qa_status is None:
        st.warning("No saved Phase 7 QA run is available. Run Automated QA first, then Final verification; the final download stays locked until both gates pass.")
    elif not export.latest_qa_is_current:
        st.warning(
            f"Latest saved QA status: {export.latest_qa_status}, but its snapshot is stale or does not match the current saved product. "
            "Run automated QA again before relying on that result."
        )
    elif export.latest_qa_status == "PASS":
        st.success("Automated QA PASS for the current snapshot. Next step: run Final verification to inspect the actual exported PDF and unlock the final download.")
    else:
        st.warning(
            f"Latest saved QA result is {export.latest_qa_status} for the current content/preview snapshot. "
            "Review the findings above. Resolve the findings, rerun Automated QA, then run Final verification."
        )

    st.caption(
        f"PDF preflight: {export.page_count} page(s) · {export.page_size_label} · {export.template_name} · "
        f"{export.included_visual_count} saved visual(s) included · {len(export.pdf_bytes) / 1024:.0f} KB."
    )
    for check in export.pdf_preflight:
        st.write(f"- {check}")
    try:
        latest_runs = product_store.list_qa_runs(product_id, user_id, limit=1)
        saved_now = product_store.get(product_id, user_id)
        latest_result = latest_runs[0].get("result_payload", {}) if latest_runs else {}
        if saved_now and saved_now.get("content_payload") and saved_now.get("blueprint_payload"):
            bp_now = ProductBlueprint.model_validate(saved_now["blueprint_payload"])
            content_now = ProductContent.model_validate(saved_now["content_payload"])
            inputs_now = ProductInputs.model_validate(saved_now["inputs_payload"])
            template_now = saved_now.get("design_template_id", "minimal_professional")
            size_now = saved_now.get("page_size", "letter")
            visuals_now = product_store.get_visual_assets(product_id, user_id)
            preview_now = render_product_html(
                content_now, template_now, size_now, subtitle=content_now.subtitle,
                product_type=bp_now.product_type, audience=bp_now.target_audience,
                evidence=inputs_now.evidence, visual_assets=visuals_now,
            )
            current_fp = qa_snapshot_fingerprint(
                bp_now, content_now, design={"template_id": template_now, "page_size": size_now},
                visual_assets=visuals_now, preview_html=preview_now,
            )
        else:
            current_fp = ""
        final_verified_current = bool(
            latest_runs
            and latest_runs[0].get("snapshot_fingerprint") == current_fp
            and latest_result.get("final_verification")
            and latest_result.get("status") == "PASS"
        )
    except Exception:
        final_verified_current = False

    if final_verified_current:
        st.success("FINAL DOWNLOAD UNLOCKED")
        st.caption("This download matches the saved snapshot that passed Final verification.")
    else:
        st.warning("FINAL DOWNLOAD LOCKED")
        st.caption("Required sequence: 1) save the current product → 2) Automated QA PASS → 3) Final verification PASS → 4) download.")

    st.download_button(
        "Download product PDF",
        data=export.pdf_bytes,
        file_name=export.filename,
        mime="application/pdf",
        key=f"product_{product_id}_download_pdf",
        help="Download is enabled only after the current saved product passes Final verification.",
        type="primary",
        disabled=not final_verified_current,
    )
    with st.expander("PDF validation scope and limitations"):
        for limitation in export.limitations:
            st.write(f"- {limitation}")


def _render_content_generation(
    product_store: ProductStore,
    user_id: str,
    product_id: str,
    inputs: ProductInputs,
    blueprint: ProductBlueprint,
    saved_product: dict | None,
) -> None:
    st.divider()
    st.subheader("Generate structured product content")
    if not saved_product or saved_product.get("status") != "approved":
        st.info("Approve the blueprint above before generating its section content.")
        return

    fingerprint = _blueprint_fingerprint(blueprint)
    raw_content = saved_product.get("content_payload")
    if raw_content:
        try:
            content = ProductContent.model_validate(raw_content)
        except Exception as exc:
            st.error(f"Saved product content failed validation ({type(exc).__name__}). It has not been overwritten; check the stored draft before continuing.")
            return
        if content.blueprint_fingerprint != fingerprint:
            st.warning("The saved content belongs to an older blueprint revision. Save/approve the current blueprint again before generating content.")
            return
        if "subtitle" not in raw_content:
            content = ProductContent.model_validate({**raw_content, "subtitle": blueprint.subtitle})
        if any(section.section_index >= len(blueprint.outline) for section in content.sections):
            st.error("Saved content contains a section outside the approved blueprint outline. It has not been overwritten.")
            return
    else:
        content = ProductContent(
            product_title=blueprint.title, subtitle=blueprint.subtitle,
            blueprint_fingerprint=fingerprint, sections=[],
        )

    completed = {section.section_index for section in content.sections}
    remaining = [index for index in range(len(blueprint.outline)) if index not in completed]
    st.caption(
        f"Generated {len(completed)} of {len(blueprint.outline)} outline sections. "
        "Each section is a separate structured AI request and is saved immediately."
    )
    if not inputs.evidence:
        st.warning("No directly linked evidence is available for citations. Generated text will remain an opportunity hypothesis and should not be read as validated demand.")

    action_cols = st.columns(2)
    if remaining and action_cols[0].button("Generate next section", type="primary", key=f"product_{product_id}_generate_next"):
        next_index = remaining[0]
        try:
            generated = generate_section_content(blueprint, next_index, inputs.evidence, inputs.reference_material)
            updated = ProductContent(
                product_title=content.product_title,
                subtitle=content.subtitle,
                blueprint_fingerprint=fingerprint,
                sections=sorted([*content.sections, generated], key=lambda item: item.section_index),
            )
            product_store.save_content(
                product_id=product_id,
                user_id=user_id,
                content_payload=updated.model_dump(mode="json"),
                change_summary=f"Generated section {next_index + 1}: {generated.title}",
            )
            st.success(f"Section {next_index + 1} generated and saved.")
            st.rerun()
        except ContentGenerationError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"The section was not saved ({type(exc).__name__}). Previously saved sections remain available; retry after checking the blueprint and database.")

    if remaining:
        with st.expander("Generate all remaining sections", expanded=False):
            st.caption(f"This makes {len(remaining)} separate AI requests. Provider usage may incur charges under your configured provider's terms.")
            if st.button("Generate all remaining sections", key=f"product_{product_id}_generate_all"):
                current_sections = list(content.sections)
                generation_error = None
                with st.status("Generating product content section by section…", expanded=True) as status:
                    for position, section_index in enumerate(remaining, start=1):
                        section = blueprint.outline[section_index]
                        st.write(f"Generating {position} of {len(remaining)}: {section.title}")
                        try:
                            generated = generate_section_content(blueprint, section_index, inputs.evidence, inputs.reference_material)
                            current_sections.append(generated)
                            snapshot = ProductContent(
                                product_title=content.product_title,
                                subtitle=content.subtitle,
                                blueprint_fingerprint=fingerprint,
                                sections=sorted(current_sections, key=lambda item: item.section_index),
                            )
                            product_store.save_content(
                                product_id=product_id,
                                user_id=user_id,
                                content_payload=snapshot.model_dump(mode="json"),
                                change_summary=f"Generated section {section_index + 1}: {section.title}",
                            )
                        except ContentGenerationError as exc:
                            generation_error = str(exc)
                            break
                        except Exception as exc:
                            generation_error = f"Content could not be saved ({type(exc).__name__}). Previously saved sections remain available."
                            break
                    status.update(
                        label="Content generation paused with an error" if generation_error else "Product content generation complete",
                        state="error" if generation_error else "complete",
                    )
                if generation_error:
                    st.error(generation_error)
                else:
                    st.success("All outline sections have been generated and saved.")
                content = ProductContent(
                    product_title=content.product_title,
                    subtitle=content.subtitle,
                    blueprint_fingerprint=fingerprint,
                    sections=sorted(current_sections, key=lambda item: item.section_index),
                )
                saved_product = product_store.get(product_id, user_id) or saved_product

    if content.sections:
        _render_content_editor(
            product_store, user_id, product_id, content, blueprint, inputs.evidence, inputs.reference_material
        )
        _render_template_engine(
            product_store, user_id, product_id, content, blueprint, inputs.evidence, saved_product
        )
        _render_quality_assurance(
            product_store, user_id, product_id, content, blueprint, inputs, saved_product
        )
        _render_final_verification(product_store, user_id, product_id, content, blueprint, inputs, saved_product)
        _render_pdf_export(product_store, user_id, product_id)
        with st.expander("Structured content blocks", expanded=False):
            _render_generated_content(content, inputs.evidence)
    if saved_product.get("content_versions"):
        with st.expander("Content version history"):
            for version in reversed(saved_product["content_versions"]):
                st.write(f"**Content v{version['version_number']}** · {version['created_at']} · {version['change_summary']}")


def render_product_generator(
    product_store: ProductStore,
    user_id: str,
    report: Report,
    opportunity_index: int,
    product_id: str,
) -> None:
    """Render the milestone Product Builder page for an authenticated opportunity."""
    if not 0 <= opportunity_index < len(report.opportunities):
        st.error("The selected opportunity is no longer available in this report.")
        return
    opportunity = report.opportunities[opportunity_index]
    source = {
        "report_id": report.id,
        "report_topic": report.topic,
        "opportunity_name": opportunity.name,
        "opportunity_promise": opportunity.promise,
        "audience": opportunity.audience,
        "format": opportunity.format,
        "problem": _matching_problem(report, opportunity).model_dump(mode="json") if _matching_problem(report, opportunity) else None,
    }
    existing = product_store.get(product_id, user_id)
    if existing and existing["source_report_id"] != report.id:
        st.error("This product draft is not linked to the selected research report.")
        return
    inputs = ProductInputs.model_validate(
        st.session_state.get(f"product_{product_id}_current_inputs")
        or (existing["inputs_payload"] if existing else build_product_inputs(report, opportunity).model_dump(mode="json"))
    )
    prefix = f"product_{product_id}"
    st.title("AI Digital Product Factory")
    is_manual_topic = report.scoring_version == "manual_topic_v1"
    st.caption("Turn your own topic brief or a research-backed opportunity into a reviewable digital product blueprint.")
    if is_manual_topic:
        st.info("Creator-supplied topic mode: the blueprint can use your description and supplied reference material, but it does not establish demand, sales, or research validation.")
    else:
        st.info("This blueprint is an evidence-supported hypothesis worth validating. It is not a claim of guaranteed demand, sales, or commercial success.")
    st.markdown(f"**Selected opportunity:** {opportunity.name}")
    st.write(f"**Research promise:** {opportunity.promise}")
    if inputs.evidence:
        st.markdown("**Supplied reference material**" if is_manual_topic else "**Linked evidence**")
        for reference in inputs.evidence:
            label = f"{reference.source_type}: {reference.source_title} · {reference.evidence_id}"
            st.markdown(f"- [{label}]({reference.url})" if reference.url else f"- {label}")
    elif not is_manual_topic:
        st.warning("No source record could be directly linked to this problem signal. The blueprint will retain the research hypothesis and should be treated as needing validation.")

    inputs = clean_product_inputs(_edit_inputs(inputs, prefix))
    st.session_state[f"{prefix}_current_inputs"] = inputs.model_dump(mode="json")
    suggested_types, suggestion_reason = recommend_product_types(inputs)
    st.markdown("**Initial format-based recommendation**")
    st.write(", ".join(suggested_types))
    st.caption(suggestion_reason + " The AI may refine this recommendation in the generated blueprint.")
    selected_type = st.selectbox(
        "Choose product type",
        PRODUCT_TYPES,
        index=PRODUCT_TYPES.index(existing["blueprint_payload"]["product_type"]) if existing and existing["blueprint_payload"] and existing["blueprint_payload"].get("product_type") in PRODUCT_TYPES else PRODUCT_TYPES.index(suggested_types[0]),
        key=f"{prefix}_selected_type",
    )

    blueprint_payload = st.session_state.get(f"{prefix}_current_blueprint") or (existing["blueprint_payload"] if existing else None)
    if st.button("Generate Product Blueprint" if not blueprint_payload else "Regenerate Product Blueprint", type="primary", key=f"{prefix}_generate"):
        try:
            generated = generate_blueprint(inputs, selected_type)
        except BlueprintGenerationError as exc:
            st.error(str(exc))
            generated = None
        except ValidationError as exc:
            logger.exception(
                "Product blueprint validation failed during generation for product_id=%s",
                product_id,
            )
            details = []
            for error in exc.errors(include_url=False):
                loc = ".".join(str(part) for part in error.get("loc", ())) or "<model>"
                details.append(f"{loc}: {error.get('msg', 'validation failed')}")
            st.error(
                "Blueprint validation failed. "
                + " | ".join(details[:8])
                + (" | …" if len(details) > 8 else "")
            )
            generated = None
        except Exception as exc:
            logger.exception(
                "Unexpected product blueprint generation failure for product_id=%s",
                product_id,
            )
            st.error(
                f"Blueprint generation failed ({type(exc).__name__}). "
                "Your selected research remains available; retry after checking the provider settings."
            )
            generated = None

        if generated is not None:
            is_new_blueprint = not bool(blueprint_payload)
            try:
                inputs_payload = inputs.model_dump(mode="json")
                blueprint_payload_to_save = generated.model_dump(mode="json")
                saved = product_store.save(
                    product_id=product_id,
                    user_id=user_id,
                    source_report_id=report.id,
                    opportunity_index=opportunity_index,
                    opportunity_name=opportunity.name,
                    source_payload=source,
                    inputs_payload=inputs_payload,
                    blueprint_payload=blueprint_payload_to_save,
                    status="draft",
                    change_summary="AI-generated product blueprint" if is_new_blueprint else "AI-regenerated product blueprint",
                    create_version=True,
                )
                st.session_state[f"{prefix}_current_blueprint"] = saved["blueprint_payload"]
                st.session_state[f"{prefix}_current_inputs"] = saved["inputs_payload"]
                st.success("Blueprint generated and saved as a new version.")
                st.rerun()
            except ValidationError as exc:
                logger.exception(
                    "Product blueprint validation failed while saving for product_id=%s",
                    product_id,
                )
                details = []
                for error in exc.errors(include_url=False):
                    loc = ".".join(str(part) for part in error.get("loc", ())) or "<model>"
                    details.append(f"{loc}: {error.get('msg', 'validation failed')}")
                st.error(
                    "Blueprint save validation failed. "
                    + " | ".join(details[:8])
                    + (" | …" if len(details) > 8 else "")
                )
            except Exception as exc:
                logger.exception(
                    "Could not save generated product blueprint for product_id=%s",
                    product_id,
                )
                st.error(
                    f"Could not save the generated blueprint ({type(exc).__name__}). "
                    "Your selected research remains available; retry after checking the database and provider settings."
                )

    if blueprint_payload:
        blueprint = ProductBlueprint.model_validate(blueprint_payload)
        st.divider()
        _page_summary(blueprint)
        st.subheader("Edit and approve")
        with st.form(f"{prefix}_blueprint_approval_form"):
            try:
                edited = _blueprint_from_editor(blueprint, prefix)
            except Exception as exc:
                st.error(f"The current blueprint could not be loaded for editing: {exc}")
                edited = blueprint
            approved = st.form_submit_button("Save edits and approve blueprint", type="primary")
        if approved:
            try:
                new_payload = edited.model_dump(mode="json")
                changed = json.dumps(new_payload, sort_keys=True) != json.dumps(blueprint.model_dump(mode="json"), sort_keys=True)
                product_store.save(
                    product_id=product_id,
                    user_id=user_id,
                    source_report_id=report.id,
                    opportunity_index=opportunity_index,
                    opportunity_name=opportunity.name,
                    source_payload=source,
                    inputs_payload=inputs.model_dump(mode="json"),
                    blueprint_payload=new_payload,
                    status="approved",
                    change_summary="User-edited approved blueprint" if changed else "Blueprint approved without content changes",
                    create_version=changed,
                )
                st.session_state[f"{prefix}_current_blueprint"] = new_payload
                st.success("Blueprint approved and saved. You can now generate structured content one section at a time.")
                st.rerun()
            except Exception as exc:
                st.error(f"The blueprint was not saved: {exc}")
        if existing and existing.get("versions"):
            with st.expander("Blueprint version history"):
                for version in reversed(existing["versions"]):
                    st.write(f"**v{version['version_number']}** · {version['created_at']} · {version['change_summary']}")
        st.divider()
        st.subheader("Visual / photo plan")
        st.caption("The builder suggests only a few visuals that can improve comprehension. It does not require a visual when one is not useful, and it does not invent facts or photo requirements.")
        ideas = suggest_visual_ideas(
            blueprint.product_type,
            blueprint.core_problem or blueprint.title,
            [section.model_dump(mode="json") for section in blueprint.outline],
        )
        if ideas:
            for idea in ideas:
                with st.container(border=True):
                    st.markdown(f"**{idea['title']}** · `{idea['placement']}`")
                    st.write(idea["reason"])
                    st.caption(f"Visual brief: {idea['brief']}")
        else:
            st.write("No strong visual need was identified. Keep the product text-first unless a real image or diagram adds clear value.")
        _render_content_generation(product_store, user_id, product_id, inputs, blueprint, existing)
    if existing:
        st.caption(f"Saved product status: **{existing['status']}** · Last updated {existing['updated_at']}")
