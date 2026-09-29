from math import log1p
from typing import Dict, List

from app.database.models import Opportunity, ProblemSignal
from app.product.validator import validate
from app.processors.objection_framework import classify_objection, enrich_opportunity, pricing_rationale, value_hook

ARCHITECT_NAMES = ["Clarity Kit", "Action System", "Prompt & Template Lab", "Decision Playbook", "Workflow Sprint"]


def architect_opportunities(topic: str, problems: List[ProblemSignal], spending: Dict, gaps: Dict, evidence_count: int) -> List[Opportunity]:
    opportunities = []
    has_observed_gap = gaps.get("status") == "observed" and bool(gaps.get("gaps"))
    base_gap = min(1.0, 0.35 + gaps.get("score", 0.0)) if has_observed_gap else 0.5
    gap_status = "observed" if has_observed_gap else "insufficient_evidence"
    # The legacy field name is retained in persisted models, but this is only a
    # capped text proxy. No positive baseline is imputed when signals are absent.
    payment_language_proxy = min(0.5, max(0.0, float(spending.get("score", 0.0))))
    # Diminishing-returns evidence coverage; volume is not source quality.
    evidence_strength = min(0.5, (log1p(max(0, evidence_count)) / log1p(20)) * 0.5)
    for idx, problem in enumerate(problems[:5]):
        bucket = problem.objection_bucket or classify_objection(problem.problem)
        problem.objection_bucket = bucket
        independent_items = problem.evidence_items or 0
        fit = min(0.75, 0.35 + min(problem.urgency, 0.5) * 0.4 + min(independent_items, 5) * 0.02)
        result = validate(fit, payment_language_proxy, base_gap, evidence_strength)
        opportunity = Opportunity(
            name=f"{topic.title()} {ARCHITECT_NAMES[idx]}",
            audience=f"People working on {topic} who report: {problem.problem.lower()}",
            promise=f"Move from {problem.problem.lower()} to a repeatable next step with less research and rework.",
            format=["step-by-step guide", "Notion or spreadsheet workspace", "prompt library", "checklists"],
            problem_fit=round(fit, 2),
            willingness_to_pay=round(payment_language_proxy, 2),
            competition_gap=round(base_gap, 2),
            competition_gap_status=gap_status,
            evidence_strength=round(evidence_strength, 2),
            validation_score=result["score"],
            pricing={"starter": "$19", "core": "$29", "premium": "$49"},
            pricing_rationale=pricing_rationale(problem, bucket),
            value_hook=value_hook(problem, bucket),
            objection_bucket=bucket,
            components=["Quick-start diagnostic", "Modular implementation workflow", "Examples for three common scenarios", "Progress tracker and review checklist"],
            differentiation=[f"Built around the observed problem-language excerpt: {problem.problem.lower()}", "Short path from insight to implementation", "Evidence links included for every major assumption"],
            risks=["Heuristic extraction can miss nuance or sarcasm", "Payment-language proxy is not demonstrated willingness to pay, a sale, or a demand forecast", "Search coverage depends on public endpoint availability"],
            next_steps=["Interview five people in the target audience", "Offer a paid pilot with the core workflow", "Measure activation, completion, and refund objections", result["label"]],
        )
        opportunities.append(enrich_opportunity(opportunity, problem))
    return sorted(opportunities, key=lambda item: item.validation_score, reverse=True)
