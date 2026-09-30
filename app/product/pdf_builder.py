from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from PIL import Image as PILImage, UnidentifiedImageError
from pypdf import PdfReader
import fitz
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

try:
    from banglapdf import BanglaParagraph, register_fonts
    _BANGLA_PDF_AVAILABLE = True
except Exception:
    BanglaParagraph = None
    register_fonts = None
    _BANGLA_PDF_AVAILABLE = False

_BANGLA_FONT_READY = False
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
    if _BANGLA_FONT_READY and BanglaParagraph is not None:
        return BanglaParagraph(text.replace("\n", "<br/>"), style, markup=True)
    return Paragraph(text.replace("\n", "<br/>"), style)


def _ensure_bangla_font_support() -> bool:
    """Enable HarfBuzz-shaped Bangla paragraphs when a compatible font is available."""
    global _BANGLA_FONT_READY
    if _BANGLA_FONT_READY:
        return True
    if not _BANGLA_PDF_AVAILABLE or register_fonts is None:
        return False
    try:
        register_fonts()
    except Exception:
        return False
    _BANGLA_FONT_READY = True
    return True


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
    bangla_ready = _ensure_bangla_font_support()
    navy_hex, accent_hex, tint_hex, body_hex = template.palette
    navy, accent, tint, body = map(colors.HexColor, (navy_hex, accent_hex, tint_hex, body_hex))
    heading_font = "BanglaB" if bangla_ready else ("Times-Bold" if template_id == "minimal_professional" else "Helvetica-Bold")
    body_font = "Bangla" if bangla_ready else "Helvetica"
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
    if bangla_ready:
        bold_names = {"DPECoverEyebrow", "DPECoverTitle", "DPESectionHeading", "DPEBlockHeading", "DPEBlockHeadingSplittable", "DPETableHeader"}
        for style in styles.values():
            style.fontName = "BanglaB" if style.name in bold_names else "Bangla"
            style.shaping = 1
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
    layout_mode: str = "reading",
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
        # Give writing-heavy templates more usable response space while keeping
        # action-oriented documents compact. This is driven by the saved design
        # template, so Workbook/Planner/Journal/Worksheet PDFs do not render
        # their writing areas exactly like an Ebook or Playbook.
        line_counts = {
            "worksheet": 6 if layout_mode == "worksheet" else 4,
            "reflection": 6 if layout_mode == "reflection_prompt" else 4,
            "exercise": 5 if layout_mode in {"worksheet", "action_worksheet"} else 3,
        }
        result.extend([Spacer(1, 5), _ResponseLines(line_counts.get(block.kind, 4)), Spacer(1, 8)])
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


def _visual_pdf_preflight(pdf_bytes: bytes, page_count: int) -> tuple[str, ...]:
    """Render every page at low resolution and run conservative visual sanity checks.

    This is intentionally a renderer-level smoke test, not a claim of human visual
    approval. It catches failed page rendering, unexpectedly tiny/empty pages, and
    content that reaches the physical page edge where clipping is more likely.
    """
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ProductPdfExportError("PDF visual preflight could not open the assembled document for rendering.") from exc
    try:
        if len(document) != page_count:
            raise ProductPdfExportError("PDF visual preflight found a page-count mismatch between the PDF reader and renderer.")
        rendered = 0
        for index, page in enumerate(document, start=1):
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
                if pixmap.width < 200 or pixmap.height < 200:
                    raise ProductPdfExportError(f"PDF visual preflight found an unexpectedly small rendered page ({index}).")
                samples = pixmap.samples
                channels = pixmap.n
                total = pixmap.width * pixmap.height
                dark = 0
                min_x = pixmap.width
                min_y = pixmap.height
                max_x = max_y = -1
                for y in range(pixmap.height):
                    row_start = y * pixmap.width * channels
                    for x in range(pixmap.width):
                        offset = row_start + x * channels
                        if max(samples[offset:offset + 3]) < 245:
                            dark += 1
                            min_x = min(min_x, x)
                            min_y = min(min_y, y)
                            max_x = max(max_x, x)
                            max_y = max(max_y, y)
                ink_ratio = dark / total if total else 0.0
                if ink_ratio < 0.0008:
                    raise ProductPdfExportError(f"PDF visual preflight found a nearly empty rendered page ({index}). Review page breaks or saved content.")
                edge_margin = max(3, round(min(pixmap.width, pixmap.height) * 0.004))
                if min_x <= edge_margin or min_y <= edge_margin or max_x >= pixmap.width - 1 - edge_margin or max_y >= pixmap.height - 1 - edge_margin:
                    raise ProductPdfExportError(f"PDF visual preflight found rendered content touching the page edge on page {index}. Review possible clipping or oversized flowables.")
                rendered += 1
            finally:
                pixmap = None
        return (
            f"Rendered {rendered} page(s) at low resolution for visual smoke testing.",
            "Rendered pages were checked for near-empty output and content touching the physical page edge.",
            "This visual preflight is heuristic; human review is still required for typography, spacing, readability, and final print/display quality.",
        )
    finally:
        document.close()


def _assemble_pdf(
    *,
    blueprint: ProductBlueprint,
    content: ProductContent,
    inputs: ProductInputs,
    design: ProductDesign,
    assets: list[dict],
) -> tuple[bytes, int, int, tuple[str, ...]]:
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
        # Keep the assembled PDF byte-stable so Final Verification can bind its
        # SHA-256 to the exact file that will later be downloaded.
        invariant=1,
    )

    toc = TableOfContents()
    toc.levelStyles = [styles["TOC"]]
    toc.dotsMinLevel = 0

    # Build the cover hierarchy first: category → title → subtitle → key promise.
    # Visuals follow the hierarchy instead of pushing the title onto a later page.
    eyebrow = _paragraph(f"{template.name} · {blueprint.product_type}", styles["CoverEyebrow"])
    title_block: list[Flowable] = [
        Spacer(1, 0.10 * inch),
        _paragraph(content.product_title, styles["CoverTitle"]),
    ]
    if content.subtitle:
        title_block.append(_paragraph(content.subtitle, styles["CoverSubtitle"]))
    title_block.extend([
        HRFlowable(width="28%", thickness=2, color=accent, hAlign="CENTER", spaceBefore=3, spaceAfter=12),
        _paragraph(f"For: {blueprint.target_audience}", styles["Body"]),
        _paragraph(f"Product type: {blueprint.product_type} · Intended outcome: {blueprint.desired_outcome}", styles["Body"]),
        _paragraph("Evidence-supported concept · validate independently", styles["Callout"]),
    ])
    cover_frame_height = page_height - top_margin - bottom_margin
    reserved = 0.45 * inch + _measure_height([eyebrow], available_width) + _measure_height(title_block, available_width)
    cover_visual_height = max(0.0, cover_frame_height - reserved - 12.0)
    cover_visuals = _cover_visual_grid(placements.get("cover", []), styles, available_width, cover_visual_height)

    story: list[Flowable] = [Spacer(1, 0.45 * inch), eyebrow]
    # KeepTogether guarantees the core cover hierarchy never splits across pages.
    story.append(KeepTogether(title_block))
    story.extend(cover_visuals)
    story.extend([
        PageBreak(),
        _paragraph("Contents", styles["SectionHeading"]),
        toc,
    ])
    ordered_sections = sorted(content.sections, key=lambda section: section.section_index)
    for index, section in enumerate(ordered_sections, start=1):
        # Start a new page only when the remaining space is too small for a
        # useful section opening. This avoids large blank areas caused by a
        # forced PageBreak while still keeping headings away from the footer.
        # Only reserve enough room for a section heading, purpose, and the
        # beginning of its first block. A larger threshold creates avoidable
        # half-empty pages for short sections.
        story.append(CondPageBreak(1.55 * inch))
        story.append(_paragraph(f"{index:02d} · {section.title}", styles["SectionHeading"]))
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
                layout_mode=template.layout_for(block.kind),
            ))

    def decorate_page(canvas, current_doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D8E0E4"))
        canvas.setLineWidth(0.5)
        page_text_font = "Bangla" if _BANGLA_FONT_READY else "Helvetica"
        if current_doc.page > 1:
            canvas.setFont(page_text_font, 7.5)
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
        page_texts = [(page.extract_text() or "").replace("\\x00", " ").strip() for page in reader.pages]
        extracted = "\n".join(page_texts)
        if content.product_title.casefold() not in extracted.casefold():
            raise ProductPdfExportError("PDF preflight could not find the saved product title in extracted document text.")
        # Catch the most common production PDF defects without pretending this is
        # a visual renderer: blank pages and accidentally duplicated page content.
        for index, page_text in enumerate(page_texts, start=1):
            if not page_text:
                raise ProductPdfExportError(f"PDF preflight found an empty page ({index}). Review page breaks or shorten the preceding block.")
        for index in range(1, len(page_texts)):
            left = " ".join(page_texts[index - 1].split())
            right = " ".join(page_texts[index].split())
            if len(left) >= 180 and len(right) >= 180:
                similarity = SequenceMatcher(None, left, right).ratio()
                if similarity >= 0.94:
                    raise ProductPdfExportError(
                        f"PDF preflight found near-duplicate consecutive pages ({index} and {index + 1}). "
                        "Review repeated content or page-break behavior before exporting."
                    )
    except ProductPdfExportError:
        raise
    except Exception as exc:
        raise ProductPdfExportError("The assembled PDF did not pass a structural read check. Try again or review saved content and visuals.") from exc
    if not pdf_bytes.startswith(b"%PDF-") or not pdf_bytes.rstrip().endswith(b"%%EOF"):
        raise ProductPdfExportError("The generated file did not pass the basic PDF file-boundary check.")
    visual_preflight = _visual_pdf_preflight(pdf_bytes, page_count)
    return pdf_bytes, page_count, visual_count, visual_preflight


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
            qa_runs = []
            qa_history_unavailable = True
        latest_qa = qa_runs[0] if qa_runs else None
        latest_qa_status = (latest_qa or {}).get("result_payload", {}).get("status")
        latest_qa_is_current = bool(latest_qa and latest_qa.get("snapshot_fingerprint") == current_fingerprint)
        if qa_history_unavailable:
            raise ProductPdfExportError("Final QA history is unavailable. Run QA again before exporting the product PDF.")
        # Automated QA is the content/preview gate. The final verification
        # workflow owns actual PDF assembly and rendering checks, so Phase 7
        # must not require a PDF-rendering result that it intentionally does
        # not run.
        if qa_history_unavailable:
            raise ProductPdfExportError("Automated QA history is unavailable. Run Automated QA again before final verification.")
        if latest_qa_status != "PASS" or not latest_qa_is_current:
            if latest_qa_status != "PASS":
                raise ProductPdfExportError("Automated QA must PASS before final verification. Fix the flagged issues, save the changes, and run QA again.")
            raise ProductPdfExportError("The saved Automated QA result is stale for the current product snapshot. Run Automated QA again before final verification.")
        pdf_bytes, page_count, visual_count, visual_preflight = _assemble_pdf(
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
            *visual_preflight,
        ),
        latest_qa_status=latest_qa_status,
        latest_qa_is_current=latest_qa_is_current,
        latest_qa_created_at=(latest_qa or {}).get("created_at"),
        qa_history_unavailable=qa_history_unavailable,
        limitations=(
            "Structural preflight plus low-resolution visual smoke testing are required; section openings use a compact conditional break to reduce avoidable blank space while keeping headings from landing at the page bottom.",
            "Automated QA must pass and match the current snapshot before export; PDF preflight additionally checks for empty and near-duplicate consecutive pages. Visual typography, exact overflow, color, accessibility, and reader-specific rendering still require human review.",
            "Bangla paragraphs use HarfBuzz-shaped Noto-compatible fonts when the optional Bangla PDF font package can initialize; if a compatible runtime font is unavailable, the PDF falls back safely to the existing base fonts and should be manually reviewed for non-Latin glyph coverage.",
            "A passing preflight or saved QA result does not guarantee accuracy, usefulness, safety, demand, sales, commercial success, or an error-free PDF.",
            "Review the downloaded PDF on its intended screen or printer before distribution; re-run content QA after edits.",
        ),
    )
