import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analyzers.gap_detector import detect_gaps
from app.analyzers.spending import analyze_spending
from app.collectors.marketplaces import MarketplaceCollector
from app.collectors.reddit import RedditCollector
from app.database.db import ReportStore
from app.database.models import Evidence, Opportunity, ProblemSignal, Report
from app.evidence_metrics import count_identified_contributors
from app.pdf.generator import generate_pdf
from app.product.architect import architect_opportunities
from app.processors.problem_miner import mine_problems
from app.ui import web_interface
from app.collectors.youtube import YouTubeCollector


def ev(text, *, url="", author="", title="Post"):
    metadata = {"author": author} if author else {}
    return Evidence(source="Reddit", title=title, text=text, url=url, metadata=metadata)


# Generic commercial vocabulary, generic costs and bare listing prices do not
# count as payment intent, historical purchase, or a willingness-to-pay result.
generic = analyze_spending([
    ev("I buy groceries every week."),
    ev("I pay for groceries every week."),
    ev("I bought a coffee this morning."),
    ev("This workflow costs me two hours each day."),
    ev("The product costs $29."),
    ev("Would you pay attention to this issue?"),
    ev("Customers are willing to pay for better tools."),
    ev("The price is $49 per month."),
])
assert generic["score"] == 0
assert generic["signals"] == []
assert len(generic["price_references"]) == 2

# First-person payment intention and reported payment are distinguished, each
# evidence record can count only once, and the score has a strict cap.
intent = analyze_spending([
    ev("I would pay $20 for a reliable meal-planning tool.", url="https://example.test/1"),
    ev("I would pay $20 for a reliable meal-planning tool again.", url="https://example.test/2"),
    ev("I already pay $9 monthly for a meal-planning app.", url="https://example.test/3"),
    ev("We purchased a template last month.", url="https://example.test/4"),
])
assert intent["evidence_items_with_payment_intent"] == 2
assert intent["evidence_items_with_reported_transactions"] == 2
assert intent["score"] == 0.5
assert {row["kind"] for row in intent["signals"]} == {"explicit_payment_intent", "reported_payment_or_purchase"}

# Repeated qualifying sentences in a single post are mentions, not distinct
# supporting evidence records or people. Repeated author identity is counted once.
sentence = "I am frustrated that I waste time planning meals and cannot keep the weekly workflow organized."
one_record = ev(sentence + " " + sentence, url="https://example.test/a", author="same-user")
other_record = ev(sentence, url="https://example.test/b", author="same-user")
problems = mine_problems([one_record, other_record])
assert len(problems) == 1
problem = problems[0]
assert problem.sentence_mentions == 3
assert problem.frequency == 3
assert problem.evidence_items == 2
assert problem.unique_contributors == 1
assert problem.urgency == 0.2
duplicate_record_problem = mine_problems([one_record, one_record])[0]
assert duplicate_record_problem.sentence_mentions == 2
assert duplicate_record_problem.evidence_items == 1
no_identity_problem = mine_problems([ev(sentence, url="https://example.test/no-id")])[0]
assert no_identity_problem.unique_contributors is None
reddit_listing = RedditCollector()._from_listing({"title": "A title", "author": "alice", "permalink": "/r/test/1", "selftext": sentence})
assert reddit_listing.metadata["author"] == "alice"
youtube_accounts = [
    Evidence(source="YouTube", title="Video 1", text="Description", metadata={"channel_id": "UC123", "channel": "Creator"}),
    Evidence(source="YouTube", title="Video 2", text="Description", metadata={"channel_id": "UC123", "channel": "Creator"}),
]
assert count_identified_contributors(youtube_accounts) == 1

# Gap-theme counts are distinct evidence items even when multiple synonymous
# mapped terms occur in one item.
gaps = detect_gaps([
    ev("This confusing and complex planner is overwhelming.", url="https://example.test/gap1"),
    ev("The interface is confusing.", url="https://example.test/gap2"),
])
clarity = next(item for item in gaps["gaps"] if item["gap"] == "clarity")
simplicity = next(item for item in gaps["gaps"] if item["gap"] == "simplicity")
assert clarity["evidence_items"] == 2
assert simplicity["evidence_items"] == 1
assert "distinct collected evidence items" in gaps["assessment"]
duplicate_gap = detect_gaps([
    ev("A confusing planner.", url="https://example.test/duplicate-gap"),
    ev("A confusing planner.", url="https://example.test/duplicate-gap"),
])
assert duplicate_gap["evidence_items_considered"] == 1
assert duplicate_gap["gaps"][0]["evidence_items"] == 1

# An empty spending record does not receive an imputed willingness-to-pay score,
# and item volume has diminishing returns rather than being sold as quality.
problem_signal = ProblemSignal(
    problem=sentence, sentence_mentions=3, evidence_items=2,
    unique_contributors=1, frequency=3, urgency=0.2,
)
opp = architect_opportunities("Meal Prep", [problem_signal], {"score": 0}, gaps, 2)[0]
assert opp.willingness_to_pay == 0
assert opp.evidence_strength < 0.5
assert opp.validation_score < 60

# The live scan summary is also explicit about evidence units and non-validation.
original_reddit_collect = RedditCollector.collect
original_marketplace_collect = MarketplaceCollector.collect
try:
    RedditCollector.collect = lambda self, topic, limit=25: [one_record, other_record]
    MarketplaceCollector.collect = lambda self, topic, limit=10: []
    scanned = web_interface.run_engine("meal prep", ["Reddit"], 5)
finally:
    RedditCollector.collect = original_reddit_collect
    MarketplaceCollector.collect = original_marketplace_collect
assert scanned.scoring_version == "evidence_proxy_v2"
assert "evidence records" in scanned.executive_summary
assert "not unique people" in scanned.executive_summary
assert "not validated demand" in scanned.executive_summary

ui = Path("app/ui/web_interface.py").read_text()
readme = Path("README.md").read_text()
assert "Problem-Language Groups" in ui
assert "Identifiable Author/Channel Accounts" in ui
assert "not measured willingness to pay" in ui
assert "payment-language proxy" in readme.lower()
assert "not calibrated against real-world sales" in readme.lower()

# Current count/score metadata survives persistence and the PDF consistently
# describes the output as heuristic, not a WTP or recurrence validation.
with TemporaryDirectory() as directory:
    directory = Path(directory)
    report = Report(
        id="evidence-scoring-regression", topic="Meal Prep",
        scoring_version="evidence_proxy_v2",
        evidence=[one_record, other_record], problems=problems,
        opportunities=[opp], gap_analysis=gaps,
        executive_summary="Extracted language patterns do not establish recurrence or demand.",
    )
    store = ReportStore(directory / "reports.db")
    store.save(report)
    loaded = store.get(report.id)
    assert loaded is not None
    assert loaded.scoring_version == "evidence_proxy_v2"
    assert loaded.problems[0].sentence_mentions == 3
    assert loaded.problems[0].evidence_items == 2
    assert loaded.problems[0].unique_contributors == 1

    pdf_path = generate_pdf(loaded, directory / "report.pdf")
    pdf_text = " ".join(subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, check=True).stdout.split()).lower()
    for phrase in (
        "evidence records", "sentence mentions", "supporting evidence items",
        "author/channel accounts", "not an outcome-validated score",
        "payment-language proxy", "heuristic only", "not observed urgency",
        "may represent accounts rather than people", "matching evidence items",
    ):
        assert phrase in pdf_text, f"Missing PDF qualifier: {phrase}"
    assert "strong candidate" not in pdf_text

# An older report without the new schema fields keeps loading but the PDF marks
# its unverified legacy score/counts instead of implying the new rubric applied.
legacy_payload = Report(id="legacy-counts", topic="Meal Prep").model_dump()
legacy_payload["problems"] = [{"problem": sentence, "frequency": 4}]
legacy_payload.pop("scoring_version")
legacy = Report.model_validate(legacy_payload)
assert legacy.scoring_version == "legacy"
assert legacy.problems[0].sentence_mentions is None
assert legacy.problems[0].evidence_items is None

print("V2.7 evidence scoring/count regression tests passed")
