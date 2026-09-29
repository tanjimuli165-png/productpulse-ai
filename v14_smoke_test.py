from pathlib import Path
from tempfile import TemporaryDirectory

from app.collectors.marketplaces import MarketplaceCollector
from app.database.models import Evidence, MarketplaceGap, ProblemSignal, Report
from app.pdf.generator import generate_pdf
from app.product.architect import architect_opportunities
from app.product.launch_suite import build_blueprint, build_launch_kit
from app.processors.cleaner import normalize_evidence
from app.processors.objection_framework import build_objection_matrix

assert MarketplaceCollector()._dedupe([], 10) == []
assert normalize_evidence([Evidence(source="Web", title="No web results found", text="No problem-focused results were returned.", metadata={})]) == []
problem = ProblemSignal(problem="I waste hours building a usable meal plan and keep making mistakes", customer_language=["I waste hours building a usable meal plan and keep making mistakes"])
problems = [problem]
opp = architect_opportunities("Meal Prep", problems, {"score": 0}, {"score": 0.2, "gaps": []}, 5)[0]
gaps = [MarketplaceGap(marketplace="Etsy", title="Meal Planner Template", price="$9", format="Printable", rating="4.2", gap_signal="Format/price benchmark only")]
blueprint = build_blueprint("Meal Prep", opp, problem)
kit = build_launch_kit("Meal Prep", opp, problem, gaps)
assert len(blueprint["modules"]) >= 4
assert blueprint["bonuses"]
assert kit["listing_description"] and len(kit["short_form_hooks"]) == 3 and kit["seo_tags"]
report = Report(id="test", topic="Meal Prep", problems=problems, opportunities=[opp], objection_matrix=build_objection_matrix(problems), sales_hooks=[opp.value_hook], marketplace_gaps=gaps, product_blueprint=blueprint, launch_kit=kit)
with TemporaryDirectory() as directory:
    path = Path(directory) / "report.pdf"
    generate_pdf(report, path)
    assert path.exists() and path.stat().st_size > 1000
empty_report = Report(id="empty", topic="Unknown")
with TemporaryDirectory() as directory:
    path = Path(directory) / "empty.pdf"
    generate_pdf(empty_report, path)
    assert path.exists() and path.stat().st_size > 1000
print("V1.4 smoke tests passed")
