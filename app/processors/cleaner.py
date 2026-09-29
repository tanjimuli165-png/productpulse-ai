import re
from typing import Iterable, List

from app.database.models import Evidence
from app.search_utils import is_fallback_message


def clean_text(value: str) -> str:
    value = re.sub(r"https?://\S+", " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:3000]


def normalize_evidence(items: Iterable[Evidence]) -> List[Evidence]:
    seen = set()
    result = []
    for item in items:
        item.title = clean_text(item.title)
        item.text = clean_text(item.text)
        if item.metadata.get("error") or is_fallback_message(item.text, item.title):
            continue
        key = (item.url or "", item.title.lower())
        if key in seen or len(item.text) < 30:
            continue
        seen.add(key)
        result.append(item)
    return result
