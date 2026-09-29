from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from PIL import Image as PILImage, UnidentifiedImageError
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    HRFlowable,
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from svglib.svglib import svg2rlg

from app.product.product_schema import (
    PAGE_SIZE_OPTIONS,
    BlueprintSection,
    EvidenceReference,
    ProductBlueprint,
    ProductContent,
    ProductDesign,
    ProductInputs,
)
from app.product.qa import qa_snapshot_fingerprint
from app.product.storage import ProductStore
from app.product.template_engine import PAGE_SIZES, get_template, render_product_html
from app.product.visual_generator import MAX_IMAGE_PIXELS, MAX_UPLOAD_BYTES


class ProductPdfExportError(ValueError):
    """A saved product is not eligible for export or could not be assembled safely."""


@dataclass(frozen=True)
class ProductPdfExport:
    pdf_bytes: bytes
    filename: str
    page_count: int
    template_name: str
    page_size_label: str
    included_visual_count: int
    pdf_preflight: tuple[str, ...]
    latest_qa_status: str | None
    latest_qa_is_current: bool
    latest_qa_created_at: str | None
    qa_history_unavailable: bool
    limitations: tuple[str, ...]


class _Checkbox(Flowable):
    def __init__(self, size: float = 9):
        super().__init__()
        self.size = size
        self.width = size + 2
        self.height = size + 2

    def draw(self) -> None:
        self.canv.setStrokeColor(colors.HexColor("#526B78"))
        self.canv.setLineWidth(0.8)
        self.canv.rect(1, 1, self.size, self.size, stroke=1, fill=0)


class _ResponseLines(Flowable):
    def __init__(self, count: int = 3, leading: float = 18):
        super().__init__()
        self.count = count
        self.leading = leading
        self.width = 1
        self.height = count * leading

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        self.width = available_width
        return self.width, self.height

    def draw(self) -> None:
        self.canv.saveState()
        self.canv.setStrokeColor(colors.HexColor("#C8D2CF"))
        self.canv.setLineWidth(0.55)
        for index in range(self.count):
            y = self.height - (index + 1) * self.leading + 3
            self.canv.line(0, y, self.width, y)
        self.canv.restoreState()


class _ScaledDrawing(Flowable):
    def __init__(self, drawing: Any, max_width: float, max_height: float):
        super().__init__()
        if not getattr(drawing, "width", 0) or not getattr(drawing, "height", 0):
            raise ProductPdfExportError("A saved vector visual has invalid dimensions. Remove and re-add it before exporting.")
        factor = min(max_width / drawing.width, max_height / drawing.height, 1.0)
        self.drawing = drawing
        self.factor = factor
        self.width = drawing.width * factor
        self.height = drawing.height * factor

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def draw(self) -> None:
        from reportlab.graphics import renderPDF

        self.canv.saveState()
        self.canv.translate((self.width - self.drawing.width * self.factor) / 2, 0)
        self.canv.scale(self.factor, self.factor)
        renderPDF.draw(self.drawing, self.canv, 0, 0)
        self.canv.restoreState()


class _ProductDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable: Flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name == "DPESectionHeading":
            title = flowable.getPlainText()
            if title != "Contents":
                self.notify("TOCEntry", (0, title, self.page))


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    from xml.sax.saxutils import escape

    text = escape(str(value or "").replace("\r\n", "\n").replace("\r", "\n"), {'"': "&quot;"})
    return Paragraph(text.replace("\n", "<br/>"), style)


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value or "")
    except ValueError:
        return ""
    return value if parts.scheme in {"http", "https"} and parts.netloc and not any(ord(ch) < 32 for ch in value) else ""


def _fingerprint(blueprint: ProductBlueprint) -> str:
    serialized = json.dumps(blueprint.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    import hashlib

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_filename(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip()).strip("-").lower()[:72]
    return f"{slug or 'product'}.pdf"


def _normalize_static_svg(svg_bytes: bytes) -> bytes | None:
    """Accept the generated static SVG subset and flatten opacity for ReportLab."""
    upper = svg_bytes.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        return None
    allowed_tags = {"svg", "title", "circle", "path", "text", "rect"}
    allowed_attrs = {
        "xmlns", "width", "height", "viewBox", "role", "aria-labelledby", "id",
        "cx", "cy", "r", "fill", "opacity", "stroke", "stroke-width", "d",
        "stroke-linecap", "stroke-linejoin", "x", "y", "text-anchor", "font-family",
        "font-size", "font-weight", "x1", "x2", "y1", "y2", "rx",
    }
    try:
        root = ET.fromstring(svg_bytes)
    except (ET.ParseError, ValueError, TypeError):
        return None
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        return None
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""
        if tag not in allowed_tags or any(key not in allowed_attrs for key in node.attrib):
            return None
        if any("url(" in str(value).lower() or "javascript:" in str(value).lower() for value in node.attrib.values()):
            return None
        opacity_value = node.attrib.pop("opacity", None)
        if opacity_value is not None:
            try:
                opacity = float(opacity_value)
            except ValueError:
                return None
            if not 0 <= opacity <= 1:
                return None
            for color_key in ("fill", "stroke"):
                color_value = node.attrib.get(color_key)
                if not color_value or color_value.lower() in {"none", "transparent"} or opacity >= 1:
                    continue
                if color_value.lower() == "white":
                    red, green, blue = (255, 255, 255)
                elif re.fullmatch(r"#[0-9a-fA-F]{6}", color_value):
                    red, green, blue = (int(color_value[index:index + 2], 16) for index in (1, 3, 5))
                else:
                    return None
                red, green, blue = (round(channel * opacity + 255 * (1 - opacity)) for channel in (red, green, blue))
                node.attrib[color_key] = f"#{red:02X}{green:02X}{blue:02X}"
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _validate_visual(asset: dict) -> None:
    raw = asset.get("content")
    mime_type = asset.get("mime_type")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ProductPdfExportError("A saved visual is empty or exceeds the 2 MB safety limit. Remove and re-add it before exporting.")
    if mime_type == "image/svg+xml":
        normalized_svg = _normalize_static_svg(raw)
        if asset.get("asset_type") == "upload" or normalized_svg is None:
            raise ProductPdfExportError("A saved vector visual did not pass the restricted SVG check. Remove and re-add it before exporting.")
        try:
            drawing = svg2rlg(BytesIO(normalized_svg))
        except Exception as exc:
            raise ProductPdfExportError("A saved vector visual could not be converted for the PDF. Remove and re-add it before exporting.") from exc
        if drawing is None:
            raise ProductPdfExportError("A saved vector visual could not be read for the PDF. Remove and re-add it before exporting.")
        return
    if mime_type not in {"image/png", "image/jpeg"} or asset.get("asset_type") != "upload":
        raise ProductPdfExportError("A saved visual uses an unsupported image format. Remove and re-add it before exporting.")
    try:
        with PILImage.open(BytesIO(raw)) as image:
            image_format = image.format
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ProductPdfExportError("A saved image exceeds the 20-megapixel safety limit. Remove and re-add it before exporting.")
            image.verify()
    except ProductPdfExportError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, PILImage.DecompressionBombError) as exc:
        raise ProductPdfExportError("A saved image could not be decoded for the PDF. Remove and re-add it before exporting.") from exc
    expected_format = "PNG" if mime_type == "image/png" else "JPEG"
    if image_format != expected_format:
        raise ProductPdfExportError("A saved image's bytes do not match its declared format. Remove and re-add it before exporting.")


def _styles(template_id: str, template: Any) -> tuple[dict[str, ParagraphStyle], colors.Color, colors.Color, colors.Color]:
    navy_hex, accent_hex, tint_hex, body_hex = template.palette
    navy, accent, tint, body = map(colors.HexColor, (navy_hex, accent_hex, tint_hex, body_hex))
    heading_font = "Times-Bold" if template_id == "minimal_professional" else "Helvetica-Bold"
    body_font = "Helvetica"
    sample = getSampleStyleSheet()
    styles = {
        "CoverEyebrow": ParagraphStyle(
            "DPECoverEyebrow", parent=sample["Normal"], fontName="Helvetica-Bold", fontSize=9,
            leading=12, textColor=accent, alignment=TA_CENTER, spaceAfter=14, uppercase=True,
        ),
        "CoverTitle": ParagraphStyle(
            "DPECoverTitle", parent=sample["Title"], fontName=heading_font, fontSize=29, leading=35,
            textColor=navy, alignment=TA_CENTER, spaceAfter=13,
        ),
        "CoverSubtitle": ParagraphStyle(
            "DPECoverSubtitle", parent=sample["Normal"], fontName=body_font, fontSize=13, leading=19,
            textColor=body, alignment=TA_CENTER, spaceAfter=18,
        ),
        "Body": ParagraphStyle(
            "DPEBody", parent=sample["BodyText"], fontName=body_font, fontSize=9.5, leading=14.2,
            textColor=body, spaceAfter=8, allowWidows=0, allowOrphans=0,
        ),
        "SectionHeading": ParagraphStyle(
            "DPESectionHeading", parent=sample["Heading1"], fontName=heading_font, fontSize=19,
            leading=24, textColor=navy, spaceBefore=3, spaceAfter=9, keepWithNext=True,
        ),
        "BlockHeading": ParagraphStyle(
            "DPEBlockHeading", parent=sample["Heading2"], fontName="Helvetica-Bold", fontSize=11.5,
            leading=15, textColor=navy, spaceBefore=8, spaceAfter=5, keepWithNext=True,
        ),
        "BlockHeadingSplittable": ParagraphStyle(
            "DPEBlockHeadingSplittable", parent=sample["Heading2"], fontName="Helvetica-Bold", fontSize=11.5,
            leading=15, textColor=navy, spaceBefore=8, spaceAfter=5, keepWithNext=False,
        ),
        "Purpose": ParagraphStyle(
            "DPEPurpose", parent=sample["BodyText"], fontName=body_font, fontSize=9,
            leading=13, textColor=colors.HexColor("#687780"), spaceAfter=12,
        ),
        "Small": ParagraphStyle(
            "DPESmall", parent=sample["BodyText"], fontName=body_font, fontSize=8,
            leading=11, textColor=body, spaceAfter=5,
        ),
        "TableHeader": ParagraphStyle(
            "DPETableHeader", parent=sample["BodyText"], fontName="Helvetica-Bold", fontSize=8,
            leading=10, textColor=colors.white, spaceAfter=0,
        ),
        "TOC": ParagraphStyle(
            "DPETOC", parent=sample["Normal"], fontName=body_font, fontSize=10,
            leading=16, textColor=body, leftIndent=0, firstLineIndent=0,
        ),
        "Callout": ParagraphStyle(
            "DPECallout", parent=sample["BodyText"], fontName=body_font, fontSize=9.5,
            leading=14, textColor=navy, backColor=tint, borderColor=accent, borderWidth=0.6,
            borderPadding=9, spaceBefore=9, spaceAfter=12,
        ),
    }
    return styles, navy, accent, tint


def _make_list(items: list[str], style: ParagraphStyle, *, numbered: bool = False, checkbox: bool = False) -> Flowable:
    if checkbox:
        rows = [[_Checkbox(), _paragraph(item, style)] for item in items]
        table = Table(rows, colWidths=[16, None], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table
    from reportlab.platypus import ListFlowable, ListItem

    numbered_format = "1" if numbered else "bullet"
    return ListFlowable(
        [ListItem(_paragraph(item, style), leftIndent=14, bulletColor=colors.HexColor("#2B756F")) for item in items],
        bulletType=numbered_format, start="1", leftIndent=17 if numbered else 15,
        bulletFontName="Helvetica", bulletFontSize=8, bulletColor=colors.HexColor("#2B756F"),
        spaceAfter=8,
    )


def _raster_flowable(asset: dict, max_width: float, max_height: float) -> Flowable:
    raw = asset["content"]
    if asset["mime_type"] == "image/svg+xml":
        drawing = svg2rlg(BytesIO(raw))
        return _ScaledDrawing(drawing, max_width, max_height)
    image = Image(BytesIO(raw), width=max_width, height=max_height, kind="proportional", hAlign="CENTER")
    return image


def _visual_flowables(
    asset: dict,
    styles: dict[str, ParagraphStyle],
    max_width: float,
    max_height: float = 2.55 * inch,
    caption: bool = True,
) -> list[Flowable]:
    flowables: list[Flowable] = [_raster_flowable(asset, max_width * 0.92, max_height)]
    title = str(asset.get("title") or "Product visual")[:120]
    if caption:
        flowables.append(Spacer(1, 4))
        flowables.append(_paragraph(title, styles["Small"]))
    flowables.append(Spacer(1, 5))
    return flowables


def _measure_height(flowables: list[Flowable], width: float) -> float:
    """Best-effort measured height of a flowable list at a given width."""
    total = 0.0
    for item in flowables:
        try:
            _, height = item.wrap(width, 0xFFFFFF)
            total += float(height)
        except Exception:
            total += 0.0
        for accessor in ("getSpaceBefore", "getSpaceAfter"):
            try:
                total += float(getattr(item, accessor)())
            except Exception:
                pass
    return total


def _cover_visual_grid(
    assets: list[dict],
    styles: dict[str, ParagraphStyle],
    available_width: float,
    available_height: float,
) -> list[Flowable]:
    """Lay cover visuals out as a bounded grid so the cover always fits one page.

    The previous build stacked every cover visual at full height, so a product
    with several saved cover visuals pushed the cover title onto a later page.
    Here the grid is scaled to the space left after the title block.
    """
    if not assets:
        return []
    columns = 2 if len(assets) > 1 else 1
    rows = math.ceil(len(assets) / columns)
    # Allowance per cell for the caption line, its spacers, and table padding.
    caption_height = 30.0
    cell_width = available_width / columns
    cell_height = max(44.0, available_height / rows)
    visual_height = max(28.0, cell_height - caption_height)
    cells: list[list[Flowable]] = []
    for asset in assets:
        cells.append(_visual_flowables(asset, styles, cell_width, visual_height))
    table_rows: list[list[Any]] = []
    for index in range(0, len(cells), columns):
        row = list(cells[index:index + columns])
        row.extend([[] for _ in range(columns - len(row))])
        table_rows.append(row)
    grid = Table(table_rows, colWidths=[cell_width] * columns, hAlign="CENTER")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return [grid, Spacer(1, 4)]


def _content_block_flowables(
    block: Any,
    *,
    styles: dict[str, ParagraphStyle],
    evidence_by_id: dict[str, EvidenceReference],
    available_width: float,
    accent: colors.Color,
    tint: colors.Color,
) -> list[Flowable]:
    result: list[Flowable] = []
    if block.title:
        # A table (or any block that can grow past one page) must not be wrapped
        # in the engine's keep-with-next group: ReportLab keeps a heading with
        # the *whole* next flowable, which pushes an over-tall table to a fresh
        # page and leaves the heading page nearly empty. Splittable blocks use a
        # heading without keepWithNext plus a conditional break so the heading
        # still never sits alone at the very bottom of a page.
        heading_style = styles["BlockHeadingSplittable"] if block.kind == "table" else styles["BlockHeading"]
        if block.kind == "table":
            result.append(CondPageBreak(0.9 * inch))
        result.append(_paragraph(block.title, heading_style))
    if block.body:
        result.append(_paragraph(block.body, styles["Body"]))
    if block.kind == "table":
        table_rows: list[list[Flowable]] = [[_paragraph(value, styles["TableHeader"]) for value in block.columns]]
        table_rows.extend([[_paragraph(value, styles["Small"]) for value in row] for row in block.rows])
        widths = [available_width / len(block.columns)] * len(block.columns)
        table = LongTable(table_rows, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), accent),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CAD4DA")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        result.extend([table, Spacer(1, 8)])
    elif block.items:
        result.extend([
            _make_list(
                block.items, styles["Body"],
                numbered=block.kind in {"steps", "action_steps"},
                checkbox=block.kind == "checklist",
            ),
        ])
    if block.kind in {"exercise", "worksheet", "reflection"}:
        result.extend([Spacer(1, 5), _ResponseLines(3), Spacer(1, 8)])
    if block.kind == "reference":
        for evidence_id in block.evidence_ids:
            reference = evidence_by_id.get(evidence_id)
            if not reference:
                continue
            label = f"{reference.source_type}: {reference.source_title} · {reference.evidence_id}"
            href = _safe_url(reference.url)
            if href:
                from xml.sax.saxutils import escape

                safe_label = escape(label)
                safe_href = escape(href, {'"': "&quot;"})
                result.append(Paragraph(f'<link href="{safe_href}" color="#2B756F">{safe_label}</link>', styles["Small"]))
            else:
                result.append(_paragraph(label, styles["Small"]))
    result.append(Spacer(1, 5))
    return result


def _asset_placements(assets: list[dict], sections: list[Any]) -> tuple[dict[str, list[dict]], int]:
    section_indices = {section.section_index for section in sections}
    by_placement: dict[str, list[dict]] = {}
    for asset in assets:
        placement = asset.get("placement", "")
        if placement != "cover":
            match = re.fullmatch(r"section:(\d{1,2})", str(placement))
            if not match or int(match.group(1)) not in section_indices:
                raise ProductPdfExportError(
                    "A saved visual points to a section that is not present in the current product. Reassign or remove it before exporting."
                )
        safe_asset = dict(asset)
        if asset.get("mime_type") == "image/svg+xml":
            normalized_svg = _normalize_static_svg(asset.get("content", b""))
            if normalized_svg is None:
                raise ProductPdfExportError("A saved vector visual did not pass the restricted SVG check. Remove and re-add it before exporting.")
            safe_asset["content"] = normalized_svg
        _validate_visual(safe_asset)
        by_placement.setdefault(str(placement), []).append(safe_asset)
    return by_placement, len(assets)


def _assemble_pdf(
    *,
    blueprint: ProductBlueprint,
    content: ProductContent,
    inputs: ProductInputs,
    design: ProductDesign,
    assets: list[dict],
) -> tuple[bytes, int, int]:
    template = get_template(design.template_id)
    page_sizes = {"letter": letter, "a4": A4}
    page_size = page_sizes[design.page_size]
    top, right, bottom, left = template.margins
    page_width, page_height = page_size
    left_margin, right_margin = left * inch, right * inch
    top_margin, bottom_margin = top * inch, bottom * inch
    available_width = page_width - left_margin - right_margin
    styles, navy, accent, tint = _styles(design.template_id, template)
    placements, visual_count = _asset_placements(assets, content.sections)
    evidence_by_id = {item.evidence_id: item for item in inputs.evidence}
    buffer = BytesIO()
    doc = _ProductDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=right_margin,
        leftMargin=left_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title=content.product_title[:240],
        subject="Research-backed product concept · evidence-supported hypothesis worth validating",
        author="AI Digital Product Factory",
        pageCompression=1,
    )

    toc = TableOfContents()
    toc.levelStyles = [styles["TOC"]]
    toc.dotsMinLevel = 0

    # Build the cover title block first so it can be measured, then size the
    # cover visuals to the space that remains. This keeps eyebrow + title +
    # subtitle on page 1 even when several cover visuals are saved.
    eyebrow = _paragraph(f"{template.name} · {blueprint.product_type}", styles["CoverEyebrow"])
    title_block: list[Flowable] = [
        Spacer(1, 0.12 * inch),
        _paragraph(content.product_title, styles["CoverTitle"]),
    ]
    if content.subtitle:
        title_block.append(_paragraph(content.subtitle, styles["CoverSubtitle"]))
    title_block.extend([
        HRFlowable(width="35%", thickness=2, color=accent, hAlign="CENTER", spaceBefore=5, spaceAfter=16),
        _paragraph(f"For: {blueprint.target_audience}", styles["Body"]),
        _paragraph(f"Product type: {blueprint.product_type}", styles["Body"]),
        _paragraph(f"Intended outcome: {blueprint.desired_outcome}", styles["Body"]),
        _paragraph("This is a research-backed product concept and evidence-supported hypothesis worth validating. It does not establish demand, sales, or commercial success.", styles["Callout"]),
    ])
    cover_frame_height = page_height - top_margin - bottom_margin
    reserved = 0.45 * inch + _measure_height([eyebrow], available_width) + _measure_height(title_block, available_width)
    # Small slack so measured text/flowable rounding cannot push the title block
    # onto a second page.
    cover_visual_height = max(0.0, cover_frame_height - reserved - 10.0)
    cover_visuals = _cover_visual_grid(placements.get("cover", []), styles, available_width, cover_visual_height)

    story: list[Flowable] = [Spacer(1, 0.45 * inch), eyebrow]
    story.extend(cover_visuals)
    # KeepTogether guarantees the eyebrow+title block never splits across pages.
    story.append(KeepTogether(title_block))
    story.extend([
        PageBreak(),
        _paragraph("Contents", styles["SectionHeading"]),
        toc,
    ])
    ordered_sections = sorted(content.sections, key=lambda section: section.section_index)
    for index, section in enumerate(ordered_sections, start=1):
        story.extend([PageBreak(), _paragraph(f"{index:02d} · {section.title}", styles["SectionHeading"])])
        story.append(_paragraph(section.purpose, styles["Purpose"]))
        for asset in placements.get(f"section:{section.section_index}", []):
            story.extend(_visual_flowables(asset, styles, available_width))
        for block in section.blocks:
            story.extend(_content_block_flowables(
                block,
                styles=styles,
                evidence_by_id=evidence_by_id,
                available_width=available_width,
                accent=accent,
                tint=tint,
            ))

    def decorate_page(canvas, current_doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D8E0E4"))
        canvas.setLineWidth(0.5)
        if current_doc.page > 1:
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(colors.HexColor("#637581"))
            canvas.drawString(left_margin, page_height - 0.34 * inch, content.product_title[:80])
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(1.4)
            canvas.line(left_margin, page_height - 0.42 * inch, page_width - right_margin, page_height - 0.42 * inch)
        canvas.setStrokeColor(colors.HexColor("#D8E0E4"))
        canvas.setLineWidth(0.5)
        canvas.line(left_margin, 0.40 * inch, page_width - right_margin, 0.40 * inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#687780"))
        canvas.drawString(left_margin, 0.25 * inch, "Opportunity hypothesis · validate independently")
        canvas.drawRightString(page_width - right_margin, 0.25 * inch, f"Page {current_doc.page}")
        canvas.restoreState()

    doc.multiBuild(story, maxPasses=5, onFirstPage=decorate_page, onLaterPages=decorate_page)
    pdf_bytes = buffer.getvalue()
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=True)
        page_count = len(reader.pages)
        if page_count < 1:
            raise ProductPdfExportError("PDF preflight found no pages in the assembled document.")
        extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
        if content.product_title.casefold() not in extracted.casefold():
            raise ProductPdfExportError("PDF preflight could not find the saved product title in extracted document text.")
    except ProductPdfExportError:
        raise
    except Exception as exc:
        raise ProductPdfExportError("The assembled PDF did not pass a structural read check. Try again or review saved content and visuals.") from exc
    if not pdf_bytes.startswith(b"%PDF-") or not pdf_bytes.rstrip().endswith(b"%%EOF"):
        raise ProductPdfExportError("The generated file did not pass the basic PDF file-boundary check.")
    return pdf_bytes, page_count, visual_count


def export_saved_product_pdf(product_id: str, user_id: str, store: ProductStore) -> ProductPdfExport:
    """Assemble an in-memory PDF only from the authenticated owner's approved saved product."""
    if not user_id:
        raise ProductPdfExportError("Sign in to export a saved product PDF.")
    product = store.get(product_id, user_id)
    if not product:
        raise ProductPdfExportError("The saved product was not found for this account.")
    if product.get("status") != "approved":
        raise ProductPdfExportError("Approve and save this product blueprint before exporting its PDF.")
    if not product.get("blueprint_payload") or not product.get("content_payload"):
        raise ProductPdfExportError("Save the approved blueprint and product content before exporting a PDF.")
    try:
        blueprint = ProductBlueprint.model_validate(product["blueprint_payload"])
        inputs = ProductInputs.model_validate(product["inputs_payload"])
        content = ProductContent.model_validate(product["content_payload"])
        design = ProductDesign.model_validate({
            "template_id": product.get("design_template_id", "minimal_professional"),
            "page_size": product.get("page_size", "letter"),
        })
    except Exception as exc:
        raise ProductPdfExportError("Saved product data or design settings failed schema validation. Review the saved draft before exporting.") from exc
    if design.page_size not in PAGE_SIZE_OPTIONS:
        raise ProductPdfExportError("Choose A4 or US Letter before exporting this product.")
    expected_indices = list(range(len(blueprint.outline)))
    saved_indices = sorted(section.section_index for section in content.sections)
    if saved_indices != expected_indices:
        raise ProductPdfExportError(
            f"Product content is incomplete: saved sections {len(saved_indices)} of {len(expected_indices)}. Generate and save every approved outline section before exporting."
        )
    if content.blueprint_fingerprint != _fingerprint(blueprint):
        raise ProductPdfExportError("Saved content belongs to a different blueprint revision. Save content for the current approved blueprint before exporting.")

    try:
        visual_assets = store.get_visual_assets(product_id, user_id)
        preview_html = render_product_html(
            content,
            design.template_id,
            design.page_size,
            subtitle=content.subtitle,
            product_type=blueprint.product_type,
            audience=blueprint.target_audience,
            evidence=inputs.evidence,
            visual_assets=visual_assets,
        )
        current_fingerprint = qa_snapshot_fingerprint(
            blueprint,
            content,
            design={"template_id": design.template_id, "page_size": design.page_size},
            visual_assets=visual_assets,
            preview_html=preview_html,
        )
        qa_history_unavailable = False
        try:
            qa_runs = store.list_qa_runs(product_id, user_id, limit=1)
        except Exception:
            # QA history is advisory and must not prevent an otherwise valid private export.
            qa_runs = []
            qa_history_unavailable = True
        latest_qa = qa_runs[0] if qa_runs else None
        latest_qa_status = (latest_qa or {}).get("result_payload", {}).get("status")
        latest_qa_is_current = bool(latest_qa and latest_qa.get("snapshot_fingerprint") == current_fingerprint)
        pdf_bytes, page_count, visual_count = _assemble_pdf(
            blueprint=blueprint,
            content=content,
            inputs=inputs,
            design=design,
            assets=visual_assets,
        )
    except ProductPdfExportError:
        raise
    except Exception as exc:
        raise ProductPdfExportError(
            f"PDF assembly failed ({type(exc).__name__}). The saved product was not changed; review content and visuals, then retry."
        ) from exc

    return ProductPdfExport(
        pdf_bytes=pdf_bytes,
        filename=_safe_filename(content.product_title),
        page_count=page_count,
        template_name=get_template(design.template_id).name,
        page_size_label=PAGE_SIZES[design.page_size]["label"],
        included_visual_count=visual_count,
        pdf_preflight=(
            "PDF opened with the structural reader.",
            f"The saved product title was found in extracted PDF text; {page_count} page(s) were assembled.",
            f"{visual_count} saved visual(s) passed export input checks.",
        ),
        latest_qa_status=latest_qa_status,
        latest_qa_is_current=latest_qa_is_current,
        latest_qa_created_at=(latest_qa or {}).get("created_at"),
        qa_history_unavailable=qa_history_unavailable,
        limitations=(
            "The structural preflight confirms a readable PDF container, non-empty page count, and title text extraction; it is not a visual page-by-page review.",
            "Existing Phase 7 QA covers selected content and indicative HTML-preview patterns, not final PDF overflow, typography, page breaks, color, accessibility, or reader-specific rendering.",
            "The current templates use ReportLab's built-in base fonts; uncommon symbols and writing systems outside their glyph coverage may need font work and manual review.",
            "A passing preflight or saved QA result does not guarantee accuracy, usefulness, safety, demand, sales, commercial success, or an error-free PDF.",
            "Review the downloaded PDF on its intended screen or printer before distribution; re-run content QA after edits.",
        ),
    )
