from __future__ import annotations


# Illustrative starting points only; these are not marketplace observations.
_FORMAT_BENCHMARKS = {
    "spreadsheet": "$29 – $49",
    "digital_template": "$19 – $29",
}


def format_price_benchmark(format_name: str) -> str | None:
    """Return a clearly separate heuristic benchmark for a recognizable format."""
    formats = {part.strip().lower() for part in str(format_name or "").split(",")}
    if formats.intersection({"excel", "spreadsheet"}):
        return _FORMAT_BENCHMARKS["spreadsheet"]
    if formats.intersection({"notion", "printable", "pdf", "planner", "template"}):
        return _FORMAT_BENCHMARKS["digital_template"]
    return None
