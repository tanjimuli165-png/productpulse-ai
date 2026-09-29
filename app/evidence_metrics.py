from __future__ import annotations

from typing import Iterable

from app.database.models import Evidence
from app.search_utils import is_fallback_message


IDENTITY_FIELDS = ("author_fullname", "author", "username", "channel_id", "channel")
ANONYMOUS_IDENTITIES = {"", "[deleted]", "deleted", "unknown", "anonymous", "none", "null"}


def is_eligible_evidence(item: Evidence) -> bool:
    return not item.metadata.get("error") and not is_fallback_message(item.title or "", item.text or "")


def evidence_record_key(item: Evidence) -> str:
    """Stable, conservative identity for a collected source record."""
    if item.url and item.url.strip():
        return f"{item.source.casefold()}:{item.url.strip()}"
    return f"{item.source.casefold()}:{(item.title or '').casefold()}:{(item.text or '').casefold()}"


def contributor_key(item: Evidence) -> str | None:
    """Return a source-local author/channel key; never claim cross-site person identity."""
    for field in IDENTITY_FIELDS:
        raw = item.metadata.get(field)
        if raw is not None and str(raw).strip().casefold() not in ANONYMOUS_IDENTITIES:
            return f"{item.source.casefold()}:{field}:{str(raw).strip().casefold()}"
    return None


def count_identified_contributors(evidence: Iterable[Evidence]) -> int | None:
    keys = {key for item in evidence if (key := contributor_key(item))}
    return len(keys) if keys else None
