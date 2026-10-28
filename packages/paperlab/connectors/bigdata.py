"""Bigdata licensed document search, deliberately distinct from numerical datasets."""

from datetime import timedelta
import httpx
from paperlab.connectors import Candidate
from paperlab.storage import digest, encode


class BigdataConnector:
    name = "bigdata"

    def __init__(self, api_key, entitlement_scope):
        self.api_key, self.scope = api_key, entitlement_scope

    def discover(self, requirement, scope):
        if not self.api_key or not requirement.query:
            return []
        base = {"start": str(requirement.start), "end": str(requirement.end)}
        queries = list(dict.fromkeys([requirement.query, requirement.description]))[:2]
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
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response = client.post(
                "https://api.bigdata.com/v1/search", json=payload, headers={"X-API-KEY": self.api_key}
            )
            if response.status_code != 200:
                # Do not expose request headers, provider response bodies, or credentials.
                raise ValueError(f"Bigdata request failed with HTTP {response.status_code}")
            data = response.json()
        docs = data.get("results", [])
        return encode(data).encode(), {
            "provider": self.name,
            "source": "https://api.bigdata.com/v1/search",
            "kind": "documents",
            "documents": len(docs),
            "request": r,
            "entitlement_scope": self.scope,
        }
