# Shared Python packages

Put reusable code here when more than one running program needs it. Keep HTTP
handling and worker lifecycle code in their respective deployables, with each
build installing only the packages it needs. `agent_harness/` owns the shared
Bedrock structured-output adapter, bounded tool calls, as-of cutoff, and run
record. `agents/strategy_agent/` owns the trading plan, price-data and
membership tools, dated decisions, and deterministic VectorBT replay.
