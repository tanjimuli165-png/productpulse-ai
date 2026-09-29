import re
from typing import Dict, List

from app.database.models import Evidence

# A bare price or a broad verb such as "buy", "pay", or "cost" is not evidence
# that a prospective customer is willing to pay. Only first-person payment intent
# or a first-person reported purchase/payment contributes to the score.
_MONEY = r"(?:[$£€]\s?\d[\d,.]*(?:\s?(?:usd|dollars?))?|\b\d[\d,.]*\s?(?:usd|dollars?|per month|/mo|monthly))"
_INTENT_PATTERNS = (
    re.compile(r"\b(?:i|we|my team|our team)\s+(?:(?:would|could|might|will)\s+(?:be\s+)?(?:willing\s+to\s+|happy\s+to\s+|gladly\s+|happily\s+)?|(?:am|are)\s+(?:willing|happy|prepared)\s+to\s+)pay\s+(?=(?:for\b|" + _MONEY + r"))", re.I),
    re.compile(r"\b(?:i|we)\s+(?:(?:am|are)\s+)?(?:willing|ready|happy|prepared)\s+to\s+pay\s+(?=(?:for\b|" + _MONEY + r"))", re.I),
    re.compile(r"\b(?:i['’]m|we['’]re)\s+(?:willing|ready|happy|prepared)\s+to\s+pay\s+(?=(?:for\b|" + _MONEY + r"))", re.I),
    re.compile(r"\b(?:i|we)\s+(?:would|could|might|will)\s+(?:spend|invest)\s+(?=" + _MONEY + r"|for\b)", re.I),
    re.compile(r"\bi['’]d\s+pay\s+(?=(?:for\b|" + _MONEY + r"))", re.I),
)
_TRANSACTION_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+(?:(?:currently|already|have|once)\s+)*(?:pay|paid|spend|spent)\s+(?:" + _MONEY + r")", re.I),
    re.compile(r"\b(?:i|we)\s+(?:(?:have|once)\s+)?(?:bought|purchased|subscribed\s+to)\s+(?:[$£€]\s?\d|\d[\d,.]*\s?(?:usd|dollars?)|(?:a|an|the)\s+(?:subscription|course|app|software|tool|product|template|planner|program|service|guide|workbook|membership))", re.I),
)
_PRICE_REFERENCE = re.compile(_MONEY, re.I)


def analyze_spending(evidence: List[Evidence]) -> Dict[str, object]:
    """Return observed payment-language signals, not a willingness-to-pay estimate.

    Each normalized evidence record can contribute at most once. The deliberately
    capped score is an internal ranking proxy; even explicit intent is not a sale,
    conversion, or validation of a price.
    """
    signals = []
    price_references = []
    intent_items = 0
    transaction_items = 0

    for item in evidence:
        if item.metadata.get("error"):
            continue
        text = f"{item.title} {item.text}"
        has_intent = any(pattern.search(text) for pattern in _INTENT_PATTERNS)
        has_transaction = any(pattern.search(text) for pattern in _TRANSACTION_PATTERNS)
        kind = None
        if has_intent:
            kind = "explicit_payment_intent"
            intent_items += 1
        elif has_transaction:
            kind = "reported_payment_or_purchase"
            transaction_items += 1

        if kind:
            signals.append({"kind": kind, "source": item.source, "text": text[:350], "url": item.url})

        if _PRICE_REFERENCE.search(text):
            price_references.append({"source": item.source, "text": text[:350], "url": item.url})

    # Intention and past transactions are proxies, not outcome data. The total
    # proxy tops out at 0.50, so it can never carry the priority score by itself.
    # Count independent normalized evidence records, not repeated phrases.
    score = min(0.4, intent_items * 0.2) + min(0.1, transaction_items * 0.05)
    interpretation = (
        "Explicit first-person payment-intent language was found in collected text. It is a proxy only—not a purchase, conversion, or validated willingness-to-pay result."
        if intent_items
        else "First-person past payment or purchase statements were found, but they do not establish future willingness to pay."
        if transaction_items
        else "No qualifying first-person payment-intent or payment/purchase statement was found. Generic price words or price mentions alone do not raise this proxy."
    )
    return {
        "score": round(score, 2),
        "signals": signals[:10],
        "price_references": price_references[:10],
        "evidence_items_with_payment_intent": intent_items,
        "evidence_items_with_reported_transactions": transaction_items,
        "interpretation": interpretation,
    }
