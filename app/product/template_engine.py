from __future__ import annotations

import base64
from dataclasses import dataclass
from html import escape
from typing import Iterable
from urllib.parse import urlsplit

from app.product.product_schema import (
    DESIGN_TEMPLATE_IDS,
    PAGE_SIZE_OPTIONS,
    EvidenceReference,
    ProductContent,
)


@dataclass(frozen=True)
class DesignTemplate:
    """One reusable document design profile; it contains no generated assets."""

    template_id: str
    name: str
    description: str
    best_for: tuple[str, ...]
    page_size: str
    margins: tuple[float, float, float, float]
    typography: tuple[str, str]
    heading_hierarchy: tuple[str, ...]
    palette: tuple[str, str, str, str]
    accent_treatment: str
    table_treatment: str
    checkbox_treatment: str
    callout_treatment: str
    footer_treatment: str
    image_placement: str
    layouts: tuple[tuple[str, str], ...]

    def layout_for(self, block_kind: str) -> str:
        return dict(self.layouts).get(block_kind, "reading")


TEMPLATES: tuple[DesignTemplate, ...] = (
    DesignTemplate(
        template_id="minimal_professional",
        name="Minimal Professional",
        description="Quiet editorial hierarchy, spacious margins, and a restrained blue-green accent.",
        best_for=("Ebook", "Guide", "Checklist", "Reference-led content"),
        page_size="letter",
        margins=(0.70, 0.68, 0.72, 0.68),
        typography=("Georgia, 'Times New Roman', serif", "Arial, Helvetica, sans-serif"),
        heading_hierarchy=("Title 30/36 pt", "Section 20/26 pt", "Block 12/18 pt"),
        palette=("#17324D", "#2B756F", "#EAF2F0", "#253746"),
        accent_treatment="Thin teal rule and small section marker",
        table_treatment="Light rules, shaded header, generous cell padding",
        checkbox_treatment="Open square with aligned text and roomy row spacing",
        callout_treatment="Pale teal panel with a fine left border",
        footer_treatment="Product title, evidence-hypothesis note, and page label",
        image_placement="Optional cover-art area above the title; saved cover and section visuals appear in the preview",
        layouts=(("steps", "numbered_steps"), ("action_steps", "numbered_steps"), ("checklist", "checkbox_list"), ("table", "data_table"), ("exercise", "worksheet"), ("worksheet", "worksheet"), ("example", "example_callout"), ("reference", "references")),
    ),
    DesignTemplate(
        template_id="modern_business",
        name="Modern Business",
        description="Confident sans-serif hierarchy, deep ink, and a crisp cobalt accent for action-oriented guides.",
        best_for=("Playbook", "Action Plan", "Challenge", "Process-led content"),
        page_size="letter",
        margins=(0.62, 0.62, 0.65, 0.62),
        typography=("Arial, Helvetica, sans-serif", "Arial, Helvetica, sans-serif"),
        heading_hierarchy=("Title 28/34 pt", "Section 19/24 pt", "Block 12/17 pt"),
        palette=("#14213D", "#2D5BBA", "#EDF3FF", "#24334A"),
        accent_treatment="Cobalt section rail and compact uppercase labels",
        table_treatment="Strong blue header band, alternating pale rows, fine grid",
        checkbox_treatment="High-contrast outlined square and compact action rows",
        callout_treatment="Cool-blue action panel with a bold label rail",
        footer_treatment="Short product title, validation note, and page label",
        image_placement="Optional cover-art area in a shallow top band; saved cover and section visuals appear in the preview",
        layouts=(("steps", "action_cards"), ("action_steps", "action_cards"), ("checklist", "action_checklist"), ("table", "banded_table"), ("exercise", "action_worksheet"), ("worksheet", "action_worksheet"), ("example", "example_callout"), ("reference", "references")),
    ),
    DesignTemplate(
        template_id="clean_workbook",
        name="Clean Workbook",
        description="Warm, practical workbook styling with generous response space and clear task markers.",
        best_for=("Workbook", "Planner", "Journal", "Tracker", "Worksheet"),
        page_size="a4",
        margins=(0.68, 0.62, 0.70, 0.62),
        typography=("'Trebuchet MS', Arial, sans-serif", "Verdana, Geneva, sans-serif"),
        heading_hierarchy=("Title 27/34 pt", "Section 19/25 pt", "Block 12/18 pt"),
        palette=("#263B35", "#B45C3C", "#F7EEE7", "#2F3C38"),
        accent_treatment="Terracotta task labels with a soft green section header",
        table_treatment="Open table with warm header fill and writing-friendly row height",
        checkbox_treatment="Large open square with extra vertical room for marking",
        callout_treatment="Warm cream prompt card with a clear response label",
        footer_treatment="Product title, response-space reminder, and page label",
        image_placement="Optional small cover-art area; saved cover and section visuals appear in the preview",
        layouts=(("steps", "numbered_steps"), ("action_steps", "numbered_steps"), ("checklist", "workbook_checklist"), ("table", "writing_table"), ("exercise", "worksheet"), ("worksheet", "worksheet"), ("reflection", "reflection_prompt"), ("example", "example_callout"), ("reference", "references")),
    ),
)

_TEMPLATE_BY_ID = {template.template_id: template for template in TEMPLATES}
PAGE_SIZES = {
    "letter": {"label": "US Letter", "width_in": 8.5, "height_in": 11.0},
    "a4": {"label": "A4", "width_in": 8.27, "height_in": 11.69},
}


def get_template(template_id: str) -> DesignTemplate:
    if template_id not in DESIGN_TEMPLATE_IDS:
        raise ValueError("Choose one of the available product design templates.")
    return _TEMPLATE_BY_ID[template_id]


def template_recommendation(product_type: str) -> tuple[str, str]:
    """Return a transparent deterministic template for every supported product type."""
    normalized = (product_type or "").strip().lower()
    mapping = {
        "ebook": ("minimal_professional", "Reading-first editorial layout with clear chapter hierarchy."),
        "guide": ("minimal_professional", "Reading-first layout with step-by-step sections and references."),
        "checklist": ("minimal_professional", "Compact checklist layout with clear completion markers."),
        "playbook": ("modern_business", "Action-focused layout for workflows, decisions, and execution."),
        "action plan": ("modern_business", "Milestone and action layout designed for execution tracking."),
        "challenge": ("modern_business", "Progress-oriented layout for repeated tasks and checkpoints."),
        "workbook": ("clean_workbook", "Writing-first layout with exercises and generous response space."),
        "planner": ("clean_workbook", "Planning pages, trackers, and review space are emphasized."),
        "journal": ("clean_workbook", "Reflection-first layout with roomy prompts and writing areas."),
        "tracker": ("clean_workbook", "Tracking tables and review areas are prioritized."),
        "template": ("clean_workbook", "Reusable workspace layout with examples and customization prompts."),
        "worksheet": ("clean_workbook", "Prompt-and-response layout with clear working areas."),
    }
    return mapping.get(normalized, ("minimal_professional", "A restrained editorial layout is a flexible starting point for this format."))


def _safe_href(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in {"http", "https"} and parts.netloc else ""


def _block_html(block, template: DesignTemplate, reference_map: dict[str, EvidenceReference]) -> str:
    role = escape(template.layout_for(block.kind), quote=True)
    title = f'<h3 class="block-title">{escape(block.title)}</h3>' if block.title else ""
    body = f'<p class="block-body">{escape(block.body)}</p>' if block.body else ""
    content = [title, body]
    if block.kind == "table":
        headings = "".join(f"<th>{escape(value)}</th>" for value in block.columns)
        rows = "".join("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>" for row in block.rows)
        content.append(f'<div class="table-wrap"><table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div>')
    elif block.items:
        if block.kind == "checklist":
            items = "".join(f'<li><span class="checkbox" aria-hidden="true">□</span><span>{escape(item)}</span></li>' for item in block.items)
            content.append(f'<ul class="checklist">{items}</ul>')
        elif block.kind in {"steps", "action_steps"}:
            items = "".join(f"<li>{escape(item)}</li>" for item in block.items)
            content.append(f'<ol class="steps">{items}</ol>')
        else:
            items = "".join(f"<li>{escape(item)}</li>" for item in block.items)
            content.append(f'<ul class="item-list">{items}</ul>')
    if block.kind in {"exercise", "worksheet", "reflection"}:
        content.append('<div class="response-lines" aria-hidden="true"><i></i><i></i><i></i></div>')
    if block.kind == "reference":
        for evidence_id in block.evidence_ids:
            reference = reference_map.get(evidence_id)
            if not reference:
                continue
            label = f"{reference.source_type}: {reference.source_title} · {reference.evidence_id}"
            href = _safe_href(reference.url)
            rendered = f'<a href="{escape(href, quote=True)}" rel="noreferrer">{escape(label)}</a>' if href else escape(label)
            content.append(f'<p class="reference-item">{rendered}</p>')
    return f'<div class="content-block layout-{role}">{"".join(content)}</div>'


def _css(template: DesignTemplate, page_size: str) -> str:
    page = PAGE_SIZES[page_size]
    width = round(page["width_in"] * 96)
    height = round(page["height_in"] * 96)
    navy, accent, tint, body = template.palette
    font_heading, font_body = template.typography
    top, right, bottom, left = template.margins
    return f"""
    <style>
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; padding: 10px; background: #edf0f3; color: {body}; font-family: {font_body}; font-size: 15px; line-height: 1.58; }}
      .dpe-document {{ max-width: {width + 28}px; margin: 0 auto; }}
      .dpe-page {{ width: min(100%, {width}px); min-height: {height}px; margin: 0 auto 20px; padding: {max(28, round(top * 96))}px {max(26, round(right * 96))}px {max(30, round(bottom * 96))}px {max(26, round(left * 96))}px; background: #fff; border: 1px solid #d9dfe5; box-shadow: 0 5px 20px rgba(26,42,58,.10); display: flex; flex-direction: column; overflow-wrap: anywhere; }}
      .cover {{ justify-content: center; background: linear-gradient(160deg,#fff 0%,{tint} 100%); }}
      .cover .eyebrow {{ color: {accent}; text-transform: uppercase; letter-spacing: .13em; font-size: 11px; font-weight: 700; }}
      h1,h2,h3 {{ font-family: {font_heading}; color: {navy}; line-height: 1.2; }}
      .cover h1 {{ font-size: 34px; margin: 16px 0 12px; }}
      .cover .subtitle {{ font-size: 18px; color: {body}; max-width: 42em; }}
      .cover .audience {{ margin-top: 26px; padding-top: 16px; border-top: 2px solid {accent}; color: {body}; }}
      .section-heading {{ margin: 0 0 6px; font-size: 25px; border-left: 5px solid {accent}; padding-left: 13px; }}
      .section-purpose {{ margin: 6px 0 24px; color: #64717d; font-size: 14px; }}
      .block-title {{ font-size: 17px; margin: 0 0 8px; }}
      .block-body {{ margin: 0 0 10px; white-space: pre-wrap; }}
      .content-block {{ margin: 0 0 19px; padding: 13px 15px; border-radius: 5px; }}
      .layout-example_callout {{ background: {tint}; border-left: 4px solid {accent}; }}
      .layout-worksheet,.layout-action_worksheet,.layout-reflection_prompt {{ background: #fafaf8; border: 1px solid #dce2df; padding: 17px; }}
      .layout-action_cards {{ background: linear-gradient(90deg,{tint},#fff 75%); border-left: 4px solid {accent}; }}
      .layout-checkbox_list,.layout-action_checklist,.layout-workbook_checklist {{ background: {tint}; }}
      ul,ol {{ margin: 8px 0 2px; padding-left: 22px; }}
      li {{ margin: 5px 0; }}
      .checklist {{ list-style: none; padding: 0; }}
      .checklist li {{ display: flex; gap: 10px; align-items: flex-start; margin: 9px 0; }}
      .checkbox {{ font-size: 19px; line-height: 1.15; color: {accent}; }}
      .steps li::marker {{ color: {accent}; font-weight: 700; }}
      .table-wrap {{ overflow-x: auto; }}
      table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
      th {{ color: #fff; background: {navy}; text-align: left; }}
      th,td {{ border: 1px solid #d5dde3; padding: 9px 10px; vertical-align: top; }}
      tr:nth-child(even) td {{ background: {tint}; }}
      .layout-writing_table td {{ height: 42px; }}
      .response-lines {{ display: grid; gap: 15px; margin-top: 16px; }}
      .response-lines i {{ display: block; border-bottom: 1px solid #cbd5d0; height: 8px; }}
      .reference-item {{ font-size: 12px; margin: 5px 0; }}
      .product-visual {{ margin: 16px auto 20px; max-width: 100%; text-align: center; }}
      .product-visual img {{ display: block; max-width: 100%; max-height: 320px; object-fit: contain; margin: 0 auto; border-radius: 8px; }}
      .product-visual figcaption {{ margin-top: 6px; color: #687780; font-size: 11px; }}
      a {{ color: {accent}; overflow-wrap: anywhere; }}
      .page-footer {{ margin-top: auto; padding-top: 16px; border-top: 1px solid #e1e5e8; display: flex; justify-content: space-between; gap: 12px; color: #687780; font-size: 10px; }}
      .page-footer span:last-child {{ white-space: nowrap; }}
      .notice {{ max-width: {width}px; margin: 2px auto 12px; color: #586574; font: 12px Arial, sans-serif; }}
      @media(max-width:760px) {{ body {{ padding: 0; background: #fff; }} .dpe-page {{ min-height: 0; padding: 28px 22px; margin-bottom: 12px; box-shadow: none; }} .cover h1 {{ font-size: 29px; }} }}
    </style>
    """


def render_product_html(
    content: ProductContent,
    template_id: str,
    page_size: str = "letter",
    *,
    subtitle: str = "",
    product_type: str = "Digital product",
    audience: str = "",
    evidence: Iterable[EvidenceReference] = (),
    visual_assets: Iterable[dict] = (),
) -> str:
    """Render a safe, deterministic, in-app design view with optional saved visuals."""
    template = get_template(template_id)
    if page_size not in PAGE_SIZE_OPTIONS:
        raise ValueError("Choose A4 or US Letter for the design canvas.")
    reference_map = {reference.evidence_id: reference for reference in evidence}
    visuals_by_placement: dict[str, list[str]] = {}
    for asset in visual_assets:
        mime_type = asset.get("mime_type")
        raw = asset.get("content")
        if mime_type not in {"image/svg+xml", "image/png", "image/jpeg"} or not isinstance(raw, bytes) or not raw or len(raw) > 2 * 1024 * 1024:
            continue
        placement = asset.get("placement", "")
        if placement != "cover" and not (isinstance(placement, str) and placement.startswith("section:") and placement[8:].isdigit()):
            continue
        source = "data:" + mime_type + ";base64," + base64.b64encode(raw).decode("ascii")
        title = escape(str(asset.get("title", "Product visual"))[:120], quote=True)
        visuals_by_placement.setdefault(placement, []).append(
            f'<figure class="product-visual"><img src="{source}" alt="{title}"/><figcaption>{title}</figcaption></figure>'
        )
    sections = sorted(content.sections, key=lambda section: section.section_index)
    total_pages = len(sections) + 1
    cover = (
        '<section class="dpe-page cover">'
        f'<div class="eyebrow">{escape(template.name)} · {escape(product_type)}</div>'
        f'<h1>{escape(content.product_title)}</h1>'
        f'<p class="subtitle">{escape(subtitle)}</p>'
        f'<p class="audience"><strong>For:</strong> {escape(audience)}</p>'
        f'{"".join(visuals_by_placement.get("cover", []))}'
        '<p>This is a research-backed product concept and evidence-supported hypothesis worth validating.</p>'
        f'<footer class="page-footer"><span>{escape(template.footer_treatment)}</span><span>1 / {total_pages}</span></footer>'
        '</section>'
    )
    rendered_sections: list[str] = []
    for page_number, section in enumerate(sections, start=2):
        blocks = "".join(_block_html(block, template, reference_map) for block in section.blocks)
        rendered_sections.append(
            '<section class="dpe-page">'
            f'<p class="eyebrow">SECTION {section.section_index + 1:02d}</p>'
            f'<h2 class="section-heading">{escape(section.title)}</h2>'
            f'<p class="section-purpose">{escape(section.purpose)}</p>'
            f'{"".join(visuals_by_placement.get(f"section:{section.section_index}", []))}'
            f'<div class="section-content">{blocks}</div>'
            f'<footer class="page-footer"><span>{escape(template.footer_treatment)}</span><span>{page_number} / {total_pages}</span></footer>'
            '</section>'
        )
    size = PAGE_SIZES[page_size]["label"]
    description = escape(template.description)
    return (
        f'<html><head><meta charset="utf-8">{_css(template, page_size)}</head><body>'
        '<p class="notice">Editable in-app preview · page geometry is indicative; final PDF pagination may differ and requires review.</p>'
        f'<main class="dpe-document" data-template-id="{escape(template_id)}" data-page-size="{escape(page_size)}">'
        f'<div class="notice">{escape(template.name)} · {size} · {description}</div>'
        f'{cover}{"".join(rendered_sections)}'
        '</main></body></html>'
    )


def design_details(template_id: str) -> dict[str, object]:
    template = get_template(template_id)
    return {
        "name": template.name,
        "description": template.description,
        "best_for": template.best_for,
        "page_size": PAGE_SIZES[template.page_size]["label"],
        "margins_in": template.margins,
        "typography": template.typography,
        "heading_hierarchy": template.heading_hierarchy,
        "palette": template.palette,
        "accent_treatment": template.accent_treatment,
        "table_treatment": template.table_treatment,
        "checkbox_treatment": template.checkbox_treatment,
        "callout_treatment": template.callout_treatment,
        "footer_treatment": template.footer_treatment,
        "image_placement": template.image_placement,
    }
