from typing import List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.database.models import Evidence
from app.config import REQUEST_TIMEOUT, USER_AGENT
from app.search_utils import broad_query, dynamic_headers


class RedditCollector(BaseCollector):
    name = "Reddit"

    def collect(self, topic: str, limit: int = 25) -> List[Evidence]:
        endpoint = "https://www.reddit.com/search.json"
        try:
            response = requests.get(endpoint, params={"q": broad_query(topic), "sort": "relevance", "t": "year", "limit": min(limit, 100)}, headers={**dynamic_headers(USER_AGENT), "Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            children = response.json().get("data", {}).get("children", [])
            results = [self._from_listing(item.get("data", {})) for item in children]
            results = [item for item in results if item is not None]
            if len(results) >= 5:
                return results[:limit]
            return self._search_fallback(topic, limit, results, note="Reddit API returned fewer than five usable results")
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return self._search_fallback(topic, limit, [], note=f"Reddit API unavailable: {exc}")

    def _from_listing(self, data: dict) -> Evidence | None:
        title = data.get("title")
        if not title:
            return None
        permalink = data.get("permalink", "")
        return Evidence(source=self.name, title=title, text=data.get("selftext") or title, url="https://www.reddit.com" + permalink if permalink.startswith("/") else permalink, published_at=str(data.get("created_utc", "")), score=float(data.get("score", 0)), metadata={"subreddit": data.get("subreddit", ""), "author": data.get("author", ""), "author_fullname": data.get("author_fullname", ""), "collection_method": "reddit_json"})

    def _search_fallback(self, topic: str, limit: int, existing: List[Evidence], note: str) -> List[Evidence]:
        queries = [
            f"{broad_query(topic)} reddit complaints",
            f"{broad_query(topic)} reddit struggles",
            f"{broad_query(topic)} reddit mistakes workaround",
        ]
        results = list(existing)
        seen_urls = {item.url for item in results if item.url}
        for query in queries:
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
                    if result_url in seen_urls:
                        continue
                    seen_urls.add(result_url)
                    results.append(Evidence(source=self.name, title=anchor.get_text(" ", strip=True), text=snippet.get_text(" ", strip=True) if snippet else anchor.get_text(" ", strip=True), url=result_url, score=1.0, metadata={"collection_method": "duckduckgo_reddit_fallback", "fallback_note": note, "query": query}))
                    if len(results) >= limit:
                        return results[:limit]
            except requests.RequestException:
                continue
        return results[:limit]


__all__ = ["RedditCollector"]
