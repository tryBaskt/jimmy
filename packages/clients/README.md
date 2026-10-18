# Shared clients

Install into the project venv from the repository root:

```sh
venv/bin/python -m pip install -r packages/clients/requirements.txt
```

Import from the repository root with `packages.clients`. The strategy research
worker can use `DynamoDBClient` to read the current stock universe and metadata,
`YFinanceClient` to download adjusted close prices, and `VectorBTClient` to
simulate caller-defined exposures. These are adapters, not the agent workflow.

The universe membership tables currently contain a snapshot of present-day
constituents. A historical backtest also needs membership and inputs as they
were known on each rebalance date to avoid look-ahead bias. Yahoo intraday
history is limited by the provider; missing prices remain missing in the client
result and must be handled by the research workflow. Exposure values passed to
VectorBT for a bar must be based only on information available before that
execution bar.

Run the offline adapter tests with:

```sh
venv/bin/python -m pytest packages/clients/tests -q
```
