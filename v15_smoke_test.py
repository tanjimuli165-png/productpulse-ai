from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from app.database.models import Evidence, MarketplaceGap, ProblemSignal, Report
from app.pdf.generator import generate_pdf
from app.product.architect import architect_opportunities
from app.product.launch_suite import build_blueprint, build_launch_kit
from app.processors.objection_framework import build_objection_matrix

problem = ProblemSignal(problem="I waste hours making meal plans and keep missing ingredients", customer_language=["I waste hours making meal plans and keep missing ingredients"])
opp = architect_opportunities("Meal Prep", [problem], {"score": 0}, {"score": 0.2, "gaps": []}, 5)[0]
gap = MarketplaceGap(marketplace="Etsy", title="Meal Planner", price="$12", format="Printable", rating="4.3", review_insights=["Buyers may need clearer setup instructions."], gap_signal="Review-derived gap")
report = Report(
    id="v15",
    topic="Meal Prep",
    evidence=[Evidence(source="Web", title="No web results found", text="No problem-focused results were returned.", metadata={})],
    problems=[problem], opportunities=[opp], objection_matrix=build_objection_matrix([problem]), sales_hooks=[opp.value_hook],
    marketplace_gaps=[gap], product_blueprint=build_blueprint("Meal Prep", opp, problem), launch_kit=build_launch_kit("Meal Prep", opp, problem, [gap]),
)
assert report.marketplace_gaps[0].price == "$12"
assert report.product_blueprint["modules"] and report.launch_kit["listing_description"]
with TemporaryDirectory() as directory:
    path = Path(directory) / "report.pdf"
    generate_pdf(report, path)
    assert path.exists() and path.stat().st_size > 1000
    text = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout.lower()
    assert "no problem-focused results" not in text
print("V1.5 smoke test passed")
