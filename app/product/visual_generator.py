from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import BytesIO
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_UPLOAD_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg"}
ASSET_TYPES = {"icon", "shape", "diagram", "upload"}
ACCENT_COLORS = {"#2B756F", "#2D5BBA", "#B45C3C"}
ICON_OPTIONS = {
    "Checkmark": "check",
    "Target": "target",
    "Arrow": "arrow",
    "Lightbulb": "lightbulb",
}
SHAPE_OPTIONS = {"Highlight card": "highlight_card", "Section marker": "section_marker", "Divider": "divider"}


class VisualGenerationError(ValueError):
    """A clear, user-correctable visual asset input or upload error."""


@dataclass(frozen=True)
class VisualAssetData:
    asset_type: str
    title: str
    mime_type: str
    filename: str
    content: bytes
    metadata: dict


def _clean_title(value: str) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise VisualGenerationError("Add a short title for this visual.")
    if len(title) > 120:
        raise VisualGenerationError("Visual titles must be 120 characters or fewer.")
    return title


def _svg_document(body: str, *, width: int = 800, height: int = 240, title: str = "Visual") -> bytes:
    safe_title = escape(title)
    source = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="visual-title">'
        f'<title id="visual-title">{safe_title}</title>{body}</svg>'
    )
    return source.encode("utf-8")


def _icon_svg(icon: str, title: str, accent: str) -> bytes:
    shapes = {
        "check": '<circle cx="80" cy="80" r="59" fill="{accent}" opacity=".12"/><circle cx="80" cy="80" r="42" fill="none" stroke="{accent}" stroke-width="8"/><path d="M57 81l16 17 34-38" fill="none" stroke="{accent}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>',
        "target": '<circle cx="80" cy="80" r="57" fill="none" stroke="{accent}" stroke-width="8"/><circle cx="80" cy="80" r="34" fill="none" stroke="{accent}" stroke-width="7" opacity=".7"/><circle cx="80" cy="80" r="10" fill="{accent}"/><path d="M82 78l43-43m-20 2h18v18" fill="none" stroke="{accent}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>',
        "arrow": '<circle cx="80" cy="80" r="59" fill="{accent}" opacity=".12"/><path d="M39 80h78m-29-29l29 29-29 29" fill="none" stroke="{accent}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>',
        "lightbulb": '<path d="M80 24c-25 0-43 18-43 42 0 17 9 28 20 40 5 5 7 10 7 17h32c0-7 2-12 7-17 11-12 20-23 20-40 0-24-18-42-43-42z" fill="{accent}" opacity=".15" stroke="{accent}" stroke-width="7"/><path d="M65 135h30m-26 13h22m-18 11h14" stroke="{accent}" stroke-width="7" stroke-linecap="round"/>',
    }
    body = shapes[icon].format(accent=accent)
    body += f'<text x="175" y="88" fill="#253746" font-family="Arial,sans-serif" font-size="26" font-weight="700">{escape(title)}</text>'
    body += f'<text x="175" y="122" fill="#64717d" font-family="Arial,sans-serif" font-size="16">{escape(icon.replace("_", " ").title())}</text>'
    return _svg_document(body, height=160, title=title)


def _shape_svg(shape: str, title: str, accent: str) -> bytes:
    label = escape(title)
    if shape == "highlight_card":
        body = (
            f'<rect x="20" y="24" width="760" height="192" rx="20" fill="{accent}" opacity=".10"/>'
            f'<rect x="20" y="24" width="9" height="192" rx="4" fill="{accent}"/>'
            f'<circle cx="78" cy="120" r="26" fill="{accent}" opacity=".18"/>'
            f'<path d="M67 120h22m-11-11v22" stroke="{accent}" stroke-width="5" stroke-linecap="round"/>'
            f'<text x="126" y="132" fill="#253746" font-family="Arial,sans-serif" font-size="26" font-weight="700">{label}</text>'
        )
    elif shape == "section_marker":
        body = (
            f'<rect x="20" y="63" width="760" height="114" rx="12" fill="white" stroke="{accent}" stroke-width="3"/>'
            f'<rect x="20" y="63" width="112" height="114" rx="12" fill="{accent}"/>'
            f'<text x="76" y="132" text-anchor="middle" fill="white" font-family="Arial,sans-serif" font-size="28" font-weight="700">01</text>'
            f'<text x="164" y="130" fill="#253746" font-family="Arial,sans-serif" font-size="26" font-weight="700">{label}</text>'
        )
    else:
        body = (
            f'<rect x="20" y="105" width="760" height="8" rx="4" fill="{accent}" opacity=".28"/>'
            f'<circle cx="40" cy="109" r="12" fill="{accent}"/>'
            f'<text x="20" y="78" fill="#253746" font-family="Arial,sans-serif" font-size="24" font-weight="700">{label}</text>'
        )
    return _svg_document(body, title=title)


def _diagram_svg(title: str, steps: list[str], accent: str) -> bytes:
    if not 2 <= len(steps) <= 5:
        raise VisualGenerationError("A process diagram needs between 2 and 5 steps, one per line.")
    cleaned = [" ".join(str(item).split()) for item in steps]
    if any(not item for item in cleaned):
        raise VisualGenerationError("Remove blank lines from the process steps.")
    if any(len(item) > 60 for item in cleaned):
        raise VisualGenerationError("Keep each process step to 60 characters or fewer.")
    width, height = 800, 220
    margin, gap = 24, 18
    node_width = (width - 2 * margin - gap * (len(cleaned) - 1)) / len(cleaned)
    boxes = []
    for index, label in enumerate(cleaned):
        x = margin + index * (node_width + gap)
        boxes.append(
            f'<rect x="{x:.1f}" y="84" width="{node_width:.1f}" height="82" rx="12" fill="{accent}" opacity=".11" stroke="{accent}" stroke-width="2"/>'
            f'<text x="{x + node_width / 2:.1f}" y="119" text-anchor="middle" fill="{accent}" font-family="Arial,sans-serif" font-size="13" font-weight="700">STEP {index + 1}</text>'
            f'<text x="{x + node_width / 2:.1f}" y="143" text-anchor="middle" fill="#253746" font-family="Arial,sans-serif" font-size="14">{escape(label)}</text>'
        )
        if index < len(cleaned) - 1:
            start_x = x + node_width + 2
            end_x = x + node_width + gap - 4
            mid_y = 125
            boxes.append(f'<path d="M{start_x:.1f} {mid_y}H{end_x:.1f}m-7-6l7 6-7 6" fill="none" stroke="{accent}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
    heading = f'<text x="24" y="44" fill="#253746" font-family="Arial,sans-serif" font-size="22" font-weight="700">{escape(title)}</text>'
    return _svg_document(heading + "".join(boxes), width=width, height=height, title=title)


def create_visual_asset(
    asset_type: str,
    title: str,
    *,
    icon: str = "check",
    shape: str = "highlight_card",
    steps: list[str] | None = None,
    accent_color: str = "#2B756F",
) -> VisualAssetData:
    """Create a small, deterministic SVG asset; no image provider is called."""
    title = _clean_title(title)
    if asset_type not in {"icon", "shape", "diagram"}:
        raise VisualGenerationError("Choose an icon, shape, or process diagram to create.")
    if accent_color not in ACCENT_COLORS:
        raise VisualGenerationError("Choose one of the template accent colors.")
    if asset_type == "icon":
        if icon not in set(ICON_OPTIONS.values()):
            raise VisualGenerationError("Choose one of the available icon styles.")
        svg = _icon_svg(icon, title, accent_color)
    elif asset_type == "shape":
        if shape not in set(SHAPE_OPTIONS.values()):
            raise VisualGenerationError("Choose one of the available simple shapes.")
        svg = _shape_svg(shape, title, accent_color)
    else:
        svg = _diagram_svg(title, list(steps or []), accent_color)
    return VisualAssetData(
        asset_type=asset_type,
        title=title,
        mime_type="image/svg+xml",
        filename=f"{asset_type}-{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:48] or 'visual'}.svg",
        content=svg,
        metadata={"generator": "deterministic_svg", "version": 1},
    )


def sanitize_uploaded_image(data: bytes, filename: str) -> VisualAssetData:
    """Validate and re-encode a small PNG/JPEG, stripping metadata and active payloads."""
    if not data:
        raise VisualGenerationError("Choose a PNG or JPEG image to upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise VisualGenerationError("Uploaded images must be 2 MB or smaller.")
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_UPLOAD_FORMATS:
                raise VisualGenerationError("Only PNG and JPEG image uploads are supported.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise VisualGenerationError("The uploaded image dimensions are too large (maximum 20 megapixels).")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image_format == "PNG":
                if image.mode not in {"RGB", "RGBA", "L", "LA"}:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                mime_type, extension = "image/png", ".png"
            else:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                output = BytesIO()
                image.save(output, format="JPEG", quality=94, optimize=True)
                mime_type, extension = "image/jpeg", ".jpg"
    except VisualGenerationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VisualGenerationError("The selected file is not a valid supported image. Try exporting it as PNG or JPEG.") from exc
    cleaned = output.getvalue()
    if len(cleaned) > MAX_UPLOAD_BYTES:
        raise VisualGenerationError("This image is larger than 2 MB after safe re-encoding. Reduce its dimensions or choose a smaller file.")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename or "upload").stem).strip("-_")[:60] or "upload"
    return VisualAssetData(
        asset_type="upload",
        title=safe_stem.replace("-", " ").replace("_", " ")[:120],
        mime_type=mime_type,
        filename=safe_stem + extension,
        content=cleaned,
        metadata={"generator": "validated_upload", "reencoded": True},
    )
