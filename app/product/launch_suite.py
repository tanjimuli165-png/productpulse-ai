from __future__ import annotations

import re
from typing import Any, Dict, List

from app.database.models import MarketplaceGap, Opportunity, ProblemSignal


QUERY_NOISE = {
    "struggles", "complaints", "mistakes", "problems", "frustrations", "workarounds",
    "youtube", "result", "reddit", "search", "site", "etsy", "gumroad", "digital", "product",
    "template", "templates", "review", "reviews", "price", "pricing",
}


def natural_customer_language(value: str, fallback: str = "the workflow") -> str:
    """Turn mined/search-shaped text into a readable sentence for customer-facing copy."""
    text = re.sub(r"^\s*(?:youtube\s+result|search\s+result|reddit\s+result)\s*:\s*", "", value or "", flags=re.I)
    text = re.sub(r"\b(?:struggles|complaints|mistakes|problems|frustrations|workarounds)(?:\s+(?:struggles|complaints|mistakes|problems|frustrations|workarounds|reviews?|prices?))+\b", "", text, flags=re.I)
    text = re.sub(r"\b(?:site\s*:\s*\S+|youtube\s+result\s+for)\b", "", text, flags=re.I)
    words = text.split()
    while words and words[0].lower().strip(".,:;!?-") in QUERY_NOISE:
        words.pop(0)
    cleaned = re.sub(r"\s+", " ", " ".join(words)).strip(" -:;,\n\t")
    if len(cleaned) < 18 or sum(word.lower().strip(".,!?;:") in QUERY_NOISE for word in cleaned.split()) >= 3:
        cleaned = fallback
    words = cleaned.split()
    if len(words) > 15:
        cleaned = " ".join(words[:14]) + " ..."
    else:
        cleaned = " ".join(words)
    cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else fallback
    if cleaned.endswith("..."):
        return cleaned
    return cleaned.rstrip(".!?") + "."


def build_blueprint(topic: str, opportunity: Opportunity, problem: ProblemSignal | None) -> Dict[str, Any]:
    pain = natural_customer_language(problem.problem if problem else opportunity.promise, fallback=f"the {topic.lower()} workflow")
    promise = natural_customer_language(opportunity.value_hook or opportunity.promise, fallback=f"make progress with the {topic.lower()} workflow")
    return {
        "product_name": opportunity.name,
        "one_sentence_promise": promise,
        "modules": [
            {"name": "Start Here", "pages": ["Outcome map", "5-minute diagnostic", "Definition of done"]},
            {"name": "Core Workflow", "pages": ["Step-by-step implementation", "Decision tree", "Common scenarios"]},
            {"name": "Execution Workspace", "pages": ["Action tracker", "Template library", "Progress review"]},
            {"name": "Troubleshooting", "pages": ["Mistake patterns", "FAQ", "Recovery checklist"]},
            {"name": "Results Review", "pages": ["Before/after scorecard", "Next-step plan", "Feedback capture"]},
        ],
        "customer_pain_anchor": pain,
        "bonuses": ["Quick-Start Checklist", "Video Walkthrough Script", "Auto-Calculator for time, cost, or progress savings", "Example filled-in workspace", "One-page printable reference sheet"],
        "day_one_build_order": ["Create the diagnostic", "Build the core workflow", "Add three realistic examples", "Add the tracker and calculator", "Run a five-person usability test"],
    }


def build_launch_kit(topic: str, opportunity: Opportunity, problem: ProblemSignal | None, marketplace_gaps: List[MarketplaceGap]) -> Dict[str, Any]:
    pain = natural_customer_language(problem.problem if problem else topic, fallback=f"making progress with {topic.lower()}").rstrip(".!?")
    outcome = natural_customer_language(opportunity.promise, fallback="complete the next step with confidence").rstrip(".!?")
    formats = ", ".join(opportunity.format[:3])
    listing = f"Stop struggling with {pain.lower()}. {opportunity.name} is a practical {formats} system that helps you {outcome.lower()}—without {opportunity.objection_bucket.lower()}. Get the quick-start diagnostic, guided workflow, templates, examples, and progress tracker so you can move from friction to a repeatable result today."
    tags = list(dict.fromkeys([topic.lower(), "digital product", "notion template", "printable planner", "workflow template", "productivity system", "small business toolkit", "instant download", "customer pain point", "action plan"]))[:13]
    hooks = [
        f"Still losing time to {pain.lower()}? Here is the 10-minute workflow that makes the next step obvious.",
        f"I turned the most frustrating part of {topic.lower()} into a plug-and-play {formats} system.",
        f"Before you buy another generic template, use this simple test to avoid {opportunity.objection_bucket.lower()} and get a result faster.",
    ]
    roi = opportunity.pricing_rationale or "The $19–$49 range is justified when the buyer can recover time, avoid repeat mistakes, or replace scattered tools with one usable workflow."
    benchmark_note = (
        f"Compared with {len(marketplace_gaps)} marketplace result(s). Scraped prices may be unavailable; "
        "any format benchmark is a heuristic estimate, not an observed listing price. Validate price and review claims before publishing."
    )
    return {"listing_description": listing, "seo_tags": tags, "short_form_hooks": hooks, "roi_justification": roi, "marketplace_benchmark_note": benchmark_note}


__all__ = ["build_blueprint", "build_launch_kit", "natural_customer_language"]
