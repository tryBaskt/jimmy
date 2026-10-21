"""Bounded run context, allowlisted tools, and an inspectable run record."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable
from uuid import uuid4


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Recorded datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Recorded object keys must be strings")
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and isfinite(value):
        return value
    raise ValueError("Run records must contain finite JSON-compatible values")


@dataclass
class RunContext:
    as_of: datetime
    max_tool_calls: int = 10
    run_id: str = field(default_factory=lambda: uuid4().hex)
    tool_calls: int = 0

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("The as-of cutoff must be timezone-aware")
        if type(self.max_tool_calls) is not int or self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer")

    def require_available_by(self, timestamp: datetime) -> None:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Data availability time must be timezone-aware")
        if timestamp > self.as_of:
            raise ValueError("The requested data is later than the run's as-of cutoff")


@dataclass
class RunRecord:
    run_id: str
    events: list[dict] = field(default_factory=list)

    def add(self, kind: str, *, as_of: datetime, details: dict) -> None:
        self.events.append({
            "kind": kind,
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "details": _json_safe(details),
        })

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "events": list(self.events)}


class ToolRegistry:
    """Execute only registered tools; handlers receive the enforced run context."""

    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self._tools: dict[str, tuple[Callable[[RunContext, dict], Any], Callable[[Any], dict]]] = {}

    def register(
        self,
        name: str,
        handler: Callable[[RunContext, dict], Any],
        summarize: Callable[[Any], dict],
    ) -> None:
        if not name or name in self._tools:
            raise ValueError(f"Invalid or duplicate tool name: {name}")
        self._tools[name] = (handler, summarize)

    def call(self, name: str, context: RunContext, arguments: dict) -> Any:
        if name not in self._tools:
            raise ValueError(f"Tool is not allowed: {name}")
        if context.run_id != self.record.run_id:
            raise ValueError("Tool call belongs to another run")
        if context.tool_calls >= context.max_tool_calls:
            raise ValueError("The run exceeded its tool-call limit")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        context.tool_calls += 1
        handler, summarize = self._tools[name]
        result = handler(context, arguments)
        self.record.add(
            "tool_call", as_of=context.as_of,
            details={"name": name, "arguments": arguments, "result": summarize(result)},
        )
        return result
