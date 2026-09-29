from collections import defaultdict
import re
from typing import List

from app.database.models import Evidence, ProblemSignal
from app.evidence_metrics import contributor_key, evidence_record_key, is_eligible_evidence

# Terms that commonly introduce an explicit pain point, complaint, workaround, or unmet need.
PROBLEM_TERMS = (
    "how do i", "can't", "cannot", "struggle", "problem", "issue", "help", "confused",
    "difficult", "expensive", "waste", "wasting", "time-consuming", "need", "looking for",
    "wish", "frustrat", "annoy", "hate", "broken", " workaround", "instead", "manually",
    "keeps failing", "doesn't work", "does not work", "hard to", "unable to", "pain point",
)
FIRST_PERSON = re.compile(r"\b(i|we|my|our|me)\b", re.I)
STOP = {"this", "that", "with", "from", "have", "what", "when", "where", "which", "about", "there", "their", "would", "could", "should", "because", "really", "just"}


def _sentences(text: str) -> List[str]:
    # Split on actual whitespace after punctuation/newlines; keep the user's wording intact.
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|[\n\r]+", text or "") if part.strip()]


def _is_explicit_pain(sentence: str) -> bool:
    low = sentence.lower()
    has_term = any(term.strip() in low for term in PROBLEM_TERMS)
    has_first_person = bool(FIRST_PERSON.search(sentence))
    complaint_shape = any(marker in low for marker in ("but ", "however", "yet ", "tried ", "currently ", "every time ", "spent "))
    return len(sentence) >= 35 and has_term and (has_first_person or complaint_shape or "problem" in low or "issue" in low)


def _group_key(sentence: str) -> str:
    words = [w for w in re.findall(r"[a-zA-Z]{4,}", sentence.lower()) if w not in STOP]
    return " ".join(words[:7]) or sentence[:80].lower()


def mine_problems(evidence: List[Evidence], limit: int = 8) -> List[ProblemSignal]:
    """Extract explicit pain language and keep mention/item/account counts distinct."""
    candidates = []
    seen_records = set()
    for item in evidence:
        if not is_eligible_evidence(item):
            continue
        record_key = evidence_record_key(item)
        if record_key in seen_records:
            continue
        seen_records.add(record_key)
        author_key = contributor_key(item)
        for sentence in _sentences(item.text):
            if _is_explicit_pain(sentence):
                candidates.append((sentence, item.url, record_key, author_key))

    groups = defaultdict(list)
    for sentence, url, record_key, author_key in candidates:
        groups[_group_key(sentence)].append((sentence, url, record_key, author_key))

    def rank(pair):
        entries = pair[1]
        unique_records = {entry[2] for entry in entries}
        return len(unique_records), len(entries), max(len(x[0]) for x in entries)

    ranked = sorted(groups.items(), key=rank, reverse=True)[:limit]
    signals = []
    for _, items in ranked:
        exact_language = []
        for sentence, _, _, _ in items:
            if sentence not in exact_language:
                exact_language.append(sentence)
        unique_evidence_items = {record_key for _, _, record_key, _ in items}
        contributors = {author_key for _, _, _, author_key in items if author_key}
        sentence_mentions = len(items)
        # Keep frequency for reports created by older application versions; new
        # output labels it explicitly as sentence mentions rather than people.
        signals.append(
            ProblemSignal(
                problem=exact_language[0],
                customer_language=exact_language[:3],
                frequency=sentence_mentions,
                sentence_mentions=sentence_mentions,
                evidence_items=len(unique_evidence_items),
                unique_contributors=len(contributors) if contributors else None,
                evidence_urls=list(dict.fromkeys(url for _, url, _, _ in items if url))[:5],
                urgency=min(0.5, len(unique_evidence_items) / 10),
            )
        )
    return signals


__all__ = ["mine_problems"]
