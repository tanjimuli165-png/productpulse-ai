import re
from collections import Counter
from typing import Dict, List

from app.database.models import Evidence
from app.evidence_metrics import evidence_record_key
from app.search_utils import is_fallback_message


GAP_TERMS = {
    "confusing": "clarity",
    "outdated": "freshness",
    "expensive": "affordability",
    "generic": "specificity",
    "template": "implementation",
    "overwhelming": "simplicity",
    "missing": "coverage",
    "slow": "speed",
    "complex": "simplicity",
}


def detect_gaps(evidence: List[Evidence]) -> Dict[str, object]:
    """Return only gap themes matched in eligible source evidence.

    The examples and provenance are copied from the source record; when there are
    no recognized terms, no example is generated and the gap dimension is unknown.
    """
    counts = Counter()
    examples = {}
    eligible_count = 0
    seen_records = set()

    for item in evidence:
        title = item.title or ""
        text = item.text or ""
        if item.metadata.get("error") or is_fallback_message(text, title):
            continue
        record_key = evidence_record_key(item)
        if record_key in seen_records:
            continue
        seen_records.add(record_key)
        eligible_count += 1
        searchable = f"{title} {text}"
        item_gaps = set()
        for term, gap in GAP_TERMS.items():
            pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
            if re.search(pattern, searchable, flags=re.IGNORECASE):
                item_gaps.add(gap)
                examples.setdefault(
                    gap,
                    {
                        "example": text[:240],
                        "source": item.source,
                        "title": title[:160],
                        "url": item.url,
                    },
                )
        for gap in item_gaps:
            counts[gap] += 1

    gap_items = [
        {"gap": gap, "evidence_items": count, "mentions": count, **examples[gap]}
        for gap, count in counts.most_common()
    ]
    observed = bool(gap_items)
    score = round(min(1.0, sum(counts.values()) / max(4, eligible_count)), 2) if observed else 0.0
    return {
        "gaps": gap_items,
        "score": score,
        "status": "observed" if observed else "insufficient_evidence",
        "evidence_items_considered": eligible_count,
        "assessment": (
            f"{len(gap_items)} gap theme(s) matched in distinct collected evidence items. These are heuristic signals, not proof of unmet demand or recurrence among people."
            if observed
            else "No recognized gap terms were found in eligible evidence. Competition-gap evidence is insufficient; no example was inferred."
        ),
    }
