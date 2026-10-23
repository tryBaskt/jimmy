from datetime import datetime, timedelta, timezone

import pytest

from packages.agent_harness.runtime import RunContext, RunRecord, ToolRegistry


def test_registry_enforces_cutoff_allowlist_and_call_budget():
    cutoff = datetime(2025, 1, 31, tzinfo=timezone.utc)
    context = RunContext(as_of=cutoff, max_tool_calls=1)
    record = RunRecord(context.run_id)
    registry = ToolRegistry(record)

    def read_data(run, arguments):
        run.require_available_by(arguments["available_at"])
        return {"value": 42}

    registry.register("read_data", read_data, lambda result: result)
    with pytest.raises(ValueError, match="not allowed"):
        registry.call("unregistered", context, {})
    with pytest.raises(ValueError, match="later"):
        registry.call("read_data", context, {"available_at": cutoff + timedelta(days=1)})
    assert record.events == []

    context.tool_calls = 0
    result = registry.call("read_data", context, {"available_at": cutoff})
    assert result == {"value": 42}
    assert record.events[0]["details"]["result"] == result
    with pytest.raises(ValueError, match="tool-call limit"):
        registry.call("read_data", context, {"available_at": cutoff})
