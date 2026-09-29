from __future__ import annotations

from typing import List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.database.models import Evidence
from app.config import REQUEST_TIMEOUT, USER_AGENT
from app.search_utils import broad_query, dynamic_headers


class SiteSearchCollector(BaseCollector):
    """Collect public search-index snippets for a named site.

    This intentionally uses public search results rather than private/API access.
    Results are research leads and must not be treated as verified customer evidence.
    """

    def __init__(self, site_name: str, domain: str):
        self.name = site_name
        self.domain = domain

    def collect(self, topic: str, limit: int = 25) -> List[Evidence]:
        queries = [
            f"site:{self.domain} {broad_query(topic)} problems",
            f"site:{self.domain} {broad_query(topic)} complaints",
            f"site:{self.domain} {broad_query(topic)} how do I",
        ]
        results: list[Evidence] = []
        seen: set[str] = set()
        for query in queries:
            if len(results) >= limit:
                break
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            try:
                response = requests.get(url, headers=dynamic_headers(USER_AGENT), timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for card in soup.select(".result"):
                    anchor = card.select_one(".result__a")
                    snippet = card.select_one(".result__snippet")
                    if not anchor:
                        continue
                    result_url = anchor.get("href", "")
                    if self.domain not in result_url.lower() or result_url in seen:
                        continue
                    seen.add(result_url)
                    title = anchor.get_text(" ", strip=True)
                    text = snippet.get_text(" ", strip=True) if snippet else title
                    results.append(Evidence(
                        source=self.name,
                        title=title[:300],
                        text=text[:12000],
                        url=result_url,
                        score=1.0,
                        metadata={
                            "collection_method": "public_search_index",
                            "query": query,
                            "source_note": "Public search-index snippet; validate the original page manually.",
                        },
                    ))
                    if len(results) >= limit:
                        break
            except requests.RequestException:
                continue
        return results[:limit]


class QuoraCollector(SiteSearchCollector):
    name = "Quora"

    def __init__(self):
        super().__init__("Quora", "quora.com")


class InstagramCollector(SiteSearchCollector):
    name = "Instagram"

    def __init__(self):
        super().__init__("Instagram", "instagram.com")


__all__ = ["SiteSearchCollector", "QuoraCollector", "InstagramCollector"]
