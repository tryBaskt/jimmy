# Strategy research runner

From the repository root, with the existing `venv` and AWS profile:

```sh
venv/bin/python -m workers.strategy_research \
  --profile siby_baskt \
  --lookback-months 3 \
  --prompt "Find the strongest momentum stocks in the S&P 500. Select up to 10 stocks each month, hold them at equal weights, and compare with SPY after trading costs."
```

The command calls Bedrock once for a validated plan, reads current `SP500`
membership from DynamoDB, downloads adjusted daily prices with yfinance, and
simulates monthly stock selections with VectorBT. Bedrock translates the prompt
into one to three validated price-ranking factors, including momentum, price
reversal, volatility, drawdown, distance from average price, and beta to SPY.
At each rebalance, it ranks stocks using the selected number of preceding calendar months and holds the
chosen stocks for the next month. There is no separate training/holdout split.
The complete result, including every monthly stock selection and excluded symbols,
is written to `strategy_research_result.json`. Use `--start YYYY-MM-DD`,
`--end YYYY-MM-DD`, `--output PATH`, and `--model-id ID` to override defaults.
The default range is roughly the last five years and the end date is exclusive.

The runner reads AWS data but writes nothing to AWS. The current membership
table is not historical point-in-time membership, so this is exploratory
research with survivorship bias. It uses only stocks with complete Yahoo price
history over the fetched range and lists omitted symbols in the result.
