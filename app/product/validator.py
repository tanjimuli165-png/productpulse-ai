from typing import Dict


SCORE_WEIGHTS = {
    "problem_fit": 0.40,
    "payment_language_proxy": 0.10,
    "competition_gap": 0.20,
    "evidence_coverage": 0.30,
}


def validate(problem_fit: float, wtp: float, competition_gap: float, evidence_strength: float) -> Dict[str, object]:
    """Calculate a transparent prioritization heuristic, never a demand forecast.

    `wtp` is retained as a parameter name for compatibility with existing callers;
    in current scans it is a capped price/payment-language proxy (0–0.50).
    """
    score = round((
        problem_fit * SCORE_WEIGHTS["problem_fit"]
        + wtp * SCORE_WEIGHTS["payment_language_proxy"]
        + competition_gap * SCORE_WEIGHTS["competition_gap"]
        + evidence_strength * SCORE_WEIGHTS["evidence_coverage"]
    ) * 100)
    label = "Higher-priority hypothesis" if score >= 60 else "Worth reviewing" if score >= 40 else "Exploratory hypothesis"
    return {
        "score": score,
        "label": label,
        "signals": {
            "problem_fit": problem_fit,
            "payment_language_proxy": wtp,
            "competition_gap": competition_gap,
            "evidence_coverage": evidence_strength,
        },
        "interpretation": "Heuristic ranking only. It is not validated willingness to pay, demand probability, or evidence of sales.",
    }
