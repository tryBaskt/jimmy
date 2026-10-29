"""Capability-based connector registry. Add connectors without changing the planner."""

from dataclasses import dataclass, field
from typing import Protocol
from paperlab.models import Requirement


@dataclass
class Candidate:
    id: str
    connector: str
    title: str
    request: dict
    scope: str
    kind: str
    caveats: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class Connector(Protocol):
    name: str

    def discover(self, requirement: Requirement, scope: str) -> list[Candidate]: ...
    def fetch(self, candidate: Candidate) -> tuple[bytes, dict]: ...


class Registry:
    def __init__(self, connectors):
        self.connectors = {c.name: c for c in connectors}

    def discover(self, requirement, scope):
        found, errors = [], []
        for connector in self.connectors.values():
            try:
                candidates = connector.discover(requirement, scope)
                found.extend(candidates)
                if not candidates:
                    errors.append({"connector": connector.name, "code": "no_direct_candidates",
                                   "kind": requirement.kind, "symbols_supplied": bool(requirement.symbols),
                                   "query_supplied": bool(requirement.query)})
            except (ValueError, RuntimeError) as exc:
                errors.append({"connector": connector.name, "error": type(exc).__name__})
        return found, errors

    def fetch(self, candidate):
        return self.connectors[candidate.connector].fetch(candidate)
