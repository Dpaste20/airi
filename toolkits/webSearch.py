import json
import os

from agno.tools.toolkit import Toolkit


def _get(obj, key, default=None):
    """SDK results may be pydantic objects or dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class WebTools(Toolkit):
    """web_search: Exa first (needs EXA_API_KEY), Firecrawl as fallback."""

    def __init__(self, num_results: int = 5, **kwargs):
        self.num_results = num_results
        self._exa = None
        if os.environ.get("EXA_API_KEY"):
            from exa_py import Exa

            self._exa = Exa()

        from firecrawl import Firecrawl

        self._firecrawl = Firecrawl(api_key=os.environ.get("FIRECRAWL_API_KEY"))

        super().__init__(
            name="web_tools",
            tools=[self.web_search],
            instructions=(
                "Use web_search for current information, news, docs, or anything you are unsure about. "
                "Cite source URLs when you rely on them."
            ),
            add_instructions=True,
            **kwargs,
        )

    def _search_exa(self, query: str, n: int) -> list[dict]:
        res = self._exa.search(query, num_results=n, contents={"highlights": True})
        return [
            {
                "title": _get(r, "title"),
                "url": _get(r, "url"),
                "published": _get(r, "published_date"),
                "snippets": _get(r, "highlights") or [],
            }
            for r in res.results
        ]

    def _search_firecrawl(self, query: str, n: int) -> list[dict]:
        res = self._firecrawl.search(query, limit=n)
        return [
            {
                "title": _get(r, "title"),
                "url": _get(r, "url"),
                "snippets": [d] if (d := _get(r, "description")) else [],
            }
            for r in (_get(res, "web") or [])
        ]

    def web_search(self, query: str, num_results: int | None = None) -> str:
        """Search the web and return JSON with title, url and relevant snippets for each result.
        Args:
            query: What to search for.
            num_results: How many results to return (default 5).
        """
        n = num_results or self.num_results
        backends = ([("exa", self._search_exa)] if self._exa else []) + [
            ("firecrawl", self._search_firecrawl)
        ]
        errors = []
        for name, fn in backends:
            try:
                results = fn(query, n)
                if results:
                    return json.dumps({"source": name, "query": query, "results": results})
                errors.append(f"{name}: no results")
            except Exception as e:
                errors.append(f"{name}: {e}")
        return json.dumps({"error": "Search failed.", "details": errors})
