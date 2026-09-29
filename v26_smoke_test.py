import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analyzers.gap_detector import detect_gaps
from app.database.db import ReportStore
from app.database.models import Evidence, Opportunity, ProblemSignal, Report
from app.pdf.generator import generate_pdf
from app.product.architect import architect_opportunities


# Generic/non-matching evidence is retained as evidence but does not create gaps.
no_match = detect_gaps([
    Evidence(source="Web", title="Meal prep overview", text="A general overview of meal preparation and weekly routines."),
])
assert no_match["status"] == "insufficient_evidence"
assert no_match["gaps"] == []
assert no_match["score"] == 0
assert "no example was inferred" in no_match["assessment"]

# Fallback/error collector messages are excluded even if they contain a gap term.
fallback = detect_gaps([
    Evidence(source="Web", title="Collection unavailable", text="The result is confusing; no web results found."),
    Evidence(source="Reddit", title="API error", text="generic results are missing", metadata={"error": "timeout"}),
])
assert fallback["status"] == "insufficient_evidence"
assert fallback["evidence_items_considered"] == 0
assert fallback["gaps"] == []

# A real term match preserves the actual excerpt and its provenance.
observed = detect_gaps([
    Evidence(source="Reddit", title="Meal planning is confusing", text="The current meal planner is confusing to use.", url="https://example.test/post/1"),
    Evidence(source="Web", title="General guide", text="A broad description with no mapped complaint terms."),
])
assert observed["status"] == "observed"
assert observed["gaps"][0]["gap"] == "clarity"
assert observed["gaps"][0]["example"] == "The current meal planner is confusing to use."
assert observed["gaps"][0]["source"] == "Reddit"
assert observed["gaps"][0]["url"] == "https://example.test/post/1"

# With no observed terms, the opportunity score receives only a neutral 0.50
# competition-gap dimension and records its insufficiency status.
problem = ProblemSignal(problem="I waste time planning meals", urgency=0.2)
unknown_opportunity = architect_opportunities("Meal Prep", [problem], {"score": 0}, no_match, 4)[0]
assert unknown_opportunity.competition_gap == 0.5
assert unknown_opportunity.competition_gap_status == "insufficient_evidence"
assert unknown_opportunity.validation_score < 70

observed_problem = ProblemSignal(problem="Meal planning is confusing", urgency=0.2)
observed_opportunity = architect_opportunities("Meal Prep", [observed_problem], {"score": 0}, observed, 4)[0]
assert observed_opportunity.competition_gap_status == "observed"
assert observed_opportunity.competition_gap > 0.5

with TemporaryDirectory() as directory:
    directory = Path(directory)
    store = ReportStore(directory / "reports.db")
    report = Report(
        id="gap-evidence-regression",
        topic="Meal Prep",
        evidence=[Evidence(source="Web", title="Meal prep overview", text="A general overview of meal preparation and weekly routines.")],
        opportunities=[unknown_opportunity],
        gap_analysis=no_match,
    )
    store.save(report)
    loaded = store.get(report.id)
    assert loaded is not None
    assert loaded.gap_analysis["status"] == "insufficient_evidence"
    assert loaded.opportunities[0].competition_gap_status == "insufficient_evidence"

    pdf_path = directory / "gap-report.pdf"
    generate_pdf(loaded, pdf_path)
    pdf_text = " ".join(subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, check=True).stdout.split()).lower()
    assert "competition-gap evidence" in pdf_text
    assert "insufficient evidence" in pdf_text
    assert "no generic examples were added" in pdf_text
    assert "neutral competition-gap value" in pdf_text

    # Older reports have no evidence audit; preserve their stored score but do
    # not present it as a verified/strong competition-gap result.
    legacy_data = unknown_opportunity.model_dump()
    legacy_data.pop("competition_gap_status")
    legacy_opportunity = Opportunity.model_validate(legacy_data)
    assert legacy_opportunity.competition_gap_status == "not_assessed"
    legacy_report = Report(id="legacy-gap-report", topic="Meal Prep", opportunities=[legacy_opportunity])
    legacy_pdf = directory / "legacy-gap-report.pdf"
    generate_pdf(legacy_report, legacy_pdf)
    legacy_text = " ".join(subprocess.run(["pdftotext", str(legacy_pdf), "-"], capture_output=True, text=True, check=True).stdout.split()).lower()
    assert "predates gap-evidence tracking" in legacy_text
    assert "legacy score; gap evidence unverified" in legacy_text

print("V2.6 evidence-only competition-gap regression test passed")
