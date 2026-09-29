from pathlib import Path
from tempfile import TemporaryDirectory

from app.database.models import Evidence, ProblemSignal, Report
from app.pdf.generator import generate_pdf
from app.processors.cleaner import normalize_evidence
from app.processors.objection_framework import build_objection_matrix, classify_objection, value_hook
from app.product.architect import architect_opportunities

bad = Evidence(source="Web", title="No web results found", text="No problem-focused results were returned for cooking.", url="https://example.com")
assert normalize_evidence([bad]) == []
assert classify_objection("I waste hours doing this manually") == "Time/Effort Friction"
assert classify_objection("It is too expensive and I cannot justify the price") == "Money/ROI Friction"
problem = ProblemSignal(problem="I am confused by the complex setup and keep making mistakes", customer_language=["I am confused by the complex setup and keep making mistakes"])
matrix = build_objection_matrix([problem])
assert matrix["Complexity/Confusion Friction"]
opportunities = architect_opportunities("Cooking", [problem], {"score": 0}, {"score": 0.2, "gaps": []}, 5)
assert opportunities[0].value_hook.startswith("How to ")
assert opportunities[0].pricing == {"starter": "$19", "core": "$29", "premium": "$49"}
report = Report(id="test", topic="Cooking", problems=[problem], opportunities=opportunities, objection_matrix=matrix, sales_hooks=[opportunities[0].value_hook])
with TemporaryDirectory() as directory:
    path = Path(directory) / "report.pdf"
    generate_pdf(report, path)
    assert path.exists() and path.stat().st_size > 1000
print("V1.3 smoke tests passed")
