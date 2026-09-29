from collections import defaultdict
from typing import Dict, List

from app.database.models import Opportunity, ProblemSignal

BUCKETS = (
    "Time/Effort Friction",
    "Money/ROI Friction",
    "Complexity/Confusion Friction",
)

BUCKET_TERMS = {
    "Time/Effort Friction": ("time", "hours", "slow", "manual", "effort", "busy", "waste", "tedious", "labor"),
    "Money/ROI Friction": ("cost", "price", "expensive", "afford", "budget", "worth", "roi", "pay", "money", "return"),
    "Complexity/Confusion Friction": ("confus", "complex", "difficult", "hard", "overwhelm", "unclear", "learn", "understand", "mistake", "broken"),
}


def classify_objection(text: str) -> str:
    low = (text or "").lower()
    scores = {bucket: sum(low.count(term) for term in terms) for bucket, terms in BUCKET_TERMS.items()}
    return max(scores, key=scores.get) if max(scores.values()) else BUCKETS[2]


def build_objection_matrix(problems: List[ProblemSignal]) -> Dict[str, List[str]]:
    matrix = {bucket: [] for bucket in BUCKETS}
    for problem in problems:
        bucket = classify_objection(" ".join([problem.problem, *problem.customer_language]))
        problem.objection_bucket = bucket
        if problem.problem not in matrix[bucket]:
            matrix[bucket].append(problem.problem)
    return matrix


def pricing_rationale(problem: ProblemSignal, bucket: str) -> str:
    if bucket == "Time/Effort Friction":
        return "The $19–$49 range is rational when the product removes a repeatable manual task: recovering even 1–3 hours of work or reducing weekly rework can justify the fee without assuming a large business ROI."
    if bucket == "Money/ROI Friction":
        return "The $19–$49 range keeps the first purchase below a typical experimentation budget while targeting a measurable cost, waste, or missed-revenue pain; validate the claimed payback with a paid pilot."
    return "The $19–$49 range is a low-risk price for reducing confusion and mistakes: a concise workflow, checklist, or template can be priced against avoided rework and faster completion rather than an unsupported revenue promise."


def desired_outcome(problem: ProblemSignal) -> str:
    text = problem.problem.strip().rstrip(".!?")
    return f"solve {text.lower()}"


def friction_phrase(bucket: str) -> str:
    return {
        "Time/Effort Friction": "hours of manual work",
        "Money/ROI Friction": "wasted spend and uncertain ROI",
        "Complexity/Confusion Friction": "confusion and avoidable mistakes",
    }[bucket]


def value_hook(problem: ProblemSignal, bucket: str) -> str:
    return f"How to {desired_outcome(problem)} without {friction_phrase(bucket)}."


def enrich_opportunity(opportunity: Opportunity, problem: ProblemSignal) -> Opportunity:
    bucket = problem.objection_bucket or classify_objection(problem.problem)
    opportunity.objection_bucket = bucket
    opportunity.value_hook = value_hook(problem, bucket)
    opportunity.pricing_rationale = pricing_rationale(problem, bucket)
    return opportunity


__all__ = ["BUCKETS", "build_objection_matrix", "classify_objection", "enrich_opportunity", "pricing_rationale", "value_hook"]
