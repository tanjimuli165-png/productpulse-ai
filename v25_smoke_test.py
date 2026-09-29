import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from app.collectors.marketplaces import MarketplaceCollector
from app.database.db import ReportStore
from app.database.models import MarketplaceGap, Opportunity, Report
from app.pdf.generator import generate_pdf
from app.product.launch_suite import build_launch_kit


# A scraped price remains an observation; an absent price remains absent.
observed = MarketplaceCollector._from_text(
    "Etsy", "Printable Meal Planner", "Printable planner listing $12.50", "https://etsy.com/item/1", "test"
)
assert observed.price == "$12.50"
assert observed.price_benchmark == "$19 – $29"

missing = MarketplaceCollector._from_text(
    "Etsy", "Notion Meal Planner", "A Notion planner for meal prep", "https://etsy.com/item/2", "test"
)
assert missing.price is None
assert missing.price_benchmark == "$19 – $29"
unknown_format = MarketplaceCollector._from_text(
    "Gumroad", "Meal Prep Resource", "A digital guide for meal prep", "https://gumroad.com/item/3", "test"
)
assert unknown_format.price is None
assert unknown_format.price_benchmark is None

# Historical fabricated ranges are migrated to the separately labeled benchmark field.
legacy_range = MarketplaceGap.model_validate({
    "marketplace": "Etsy", "title": "Legacy Notion planner", "price": "$19 – $29", "format": "Notion"
})
assert legacy_range.price is None
assert legacy_range.price_benchmark == "$19 – $29"
legacy_missing = MarketplaceGap.model_validate({
    "marketplace": "Etsy", "title": "Legacy planner", "price": "Not found", "format": "Planner"
})
assert legacy_missing.price is None
assert legacy_missing.price_benchmark == "$19 – $29"

with TemporaryDirectory() as directory:
    store = ReportStore(Path(directory) / "reports.db")
    opportunity = Opportunity(
        name="Meal Prep Kit", audience="home cooks", promise="Plan meals more easily", format=["Notion"],
        problem_fit=0.5, willingness_to_pay=0.5, competition_gap=0.5, evidence_strength=0.5,
        validation_score=50, pricing={"starter": "$19", "core": "$29", "premium": "$49"},
        components=[], differentiation=[], risks=[], next_steps=[],
    )
    launch_kit = build_launch_kit("Meal prep", opportunity, None, [missing])
    assert "heuristic estimate, not an observed listing price" in launch_kit["marketplace_benchmark_note"]
    report = Report(id="price-regression", topic="Meal prep", marketplace_gaps=[legacy_range, observed, missing], launch_kit=launch_kit)
    store.save(report)
    loaded = store.get("price-regression")
    assert loaded is not None
    assert loaded.marketplace_gaps[0].price is None
    assert loaded.marketplace_gaps[0].price_benchmark == "$19 – $29"
    assert loaded.marketplace_gaps[1].price == "$12.50"

    pdf_path = Path(directory) / "report.pdf"
    generate_pdf(loaded, pdf_path)
    pdf_text = " ".join(subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, check=True).stdout.split())
    assert "Observed price" in pdf_text
    assert "Benchmark estimate" in pdf_text
    assert "Not found" in pdf_text
    assert "not an observed marketplace price" in pdf_text
    assert "$12.50" in pdf_text
    assert "heuristic estimate, not an observed listing price" in pdf_text

print("V2.5 observed-versus-estimated marketplace pricing regression test passed")
