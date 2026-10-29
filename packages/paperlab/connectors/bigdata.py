"""Bigdata licensed document search, deliberately distinct from numerical datasets."""

from datetime import timedelta
from paperlab.connectors import Candidate
from paperlab.storage import digest, encode
from paperlab.connectors.bigdata_mcp import BigdataMCP


class BigdataConnector:
    name = "bigdata"

    def __init__(self, api_key, entitlement_scope, mcp=None):
        self.api_key, self.scope = api_key, entitlement_scope
        self.mcp = mcp or BigdataMCP(api_key)

    def discover(self, requirement, scope):
        if not self.api_key or requirement.kind not in {"documents", "fundamentals"}:
            return []
        base = {"start": str(requirement.start), "end": str(requirement.end)}
        queries = list(dict.fromkeys([requirement.query or requirement.description, requirement.description]))[:2]
        return [
            Candidate(
                id=digest({**base, "query": query}),
                connector=self.name,
                title=f"Bigdata document evidence: {query[:100]}",
                request={**base, "query": query},
                scope=self.scope,
                kind="documents",
                caveats=[
                    "Licensed document results are evidence, not a complete numerical time series.",
                    "Search relevance does not establish historical coverage or point-in-time availability.",
                ],
            )
            for query in queries
            if query.strip()
        ]

    def fetch(self, candidate):
        r = candidate.request
        end = __import__("datetime").date.fromisoformat(r["end"]) + timedelta(days=1)
        payload = {
            "search_mode": "fast",
            "query": {
                "text": r["query"],
                "max_chunks": 50,
                "filters": {
                    "timestamp": {"start": r["start"] + "T00:00:00Z", "end": str(end) + "T00:00:00Z"}
                },
            },
        }
        data = self.mcp.call('bigdata_search', {'request': payload})
        docs = data.get("results", [])
        return encode(data).encode(), {
            "provider": self.name, "transport": "mcp", "tool": "bigdata_search",
            "source": "https://mcp.bigdata.com/", "kind": "documents",
            "documents": len(docs), "request": r, "entitlement_scope": self.scope,
        }
