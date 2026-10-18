# Stock universe snapshots

Retrieved October 17, 2026 (America/New_York). These are current-source snapshots, not point-in-time historical backtesting data.

Each text file contains sorted, unique uppercase ticker symbols, one per line, with no header. Share-class separators are normalized to hyphens (BRK.B / BRK B -> BRK-B). Symbols have not yet been checked against FMP.

The Russell files are APPROVED ETF-HOLDINGS PROXIES, not official full index constituent lists. IWB, IWM, and IWV can hold a sample of an index and can differ from official membership. In particular, the Russell 3000 proxy is not guaranteed to equal the union of the other two proxies. Do not label these as exact Russell membership in ingestion or the UI.

| File | Symbols | Source date |
|---|---:|---|
| [sp500_20261015.txt](sp500_20261015.txt) | 503 | Page last edited 2026-10-14T03:35:16Z |
| [nasdaq100_20261015.txt](nasdaq100_20261015.txt) | 101 | Page last edited 2026-10-10T02:25:36Z |
| [russell1000_proxy_20261015.txt](russell1000_proxy_20261015.txt) | 1021 | Fund Holdings as of,"Oct 15, 2026" |
| [russell2000_proxy_20261015.txt](russell2000_proxy_20261015.txt) | 1970 | Fund Holdings as of,"Oct 15, 2026" |
| [russell3000_proxy_20261015.txt](russell3000_proxy_20261015.txt) | 2584 | Fund Holdings as of,"Oct 15, 2026" |

## Sources and extraction

S&P 500 and Nasdaq-100: extracted the current component table from the linked Wikipedia pages. Page edit dates above are not guaranteed membership effective dates. Multiple share classes explain why ticker counts can exceed the company count in an index.

Russell proxies: retained listed equity rows from the official iShares CSVs. Excluded cash, money-market funds, collateral, futures, missing ticker placeholders, an unlisted escrow security, and UWM when-issued rights. Kept listed share classes separately. No symbols were invented to force the nominal index count.

### sp500_20261015.txt

- Source: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
- Output SHA-256: `40ece9e52d64177b46e467f709b72d1457f56c3598069eab3bb3b7c13fe7a72c`

### nasdaq100_20261015.txt

- Source: https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies
- Output SHA-256: `28b5446f0509e9368000641cf63a096bc726585a502c467f40e2fd7923cb8259`

### russell1000_proxy_20261015.txt

- Source: https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/latest-holdings.csv
- Output SHA-256: `f6f9368e79f6ca8fc8449993e4a9f9299ed872f89935ce3a1d3e0d65971e119d`
- Excluded equity-labelled rows:
  - `HOLX` — HOLOGIC INC
  - `UWMC-R-W` — UWM HOLDINGS RIGHTS WHEN ISSUED EX

### russell2000_proxy_20261015.txt

- Source: https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/latest-holdings.csv
- Output SHA-256: `8d2e86ac9c2985756e2cf4a96b0b941fefdc225851d24ed89bf639f35afb367f`
- Excluded equity-labelled rows:
  - `INH` — INHIBRX INC CVR
  - `ADRO` — CHINOOK THERAPEUTICS INC
  - `ADRO` — CHINOOK THERAPEUTICS INC CVR
  - `VUECF` — VERDERA ENERGY CORP
  - `-` — ARCELLX INC CVR
  - `P5N994` — Petrocorp Inc Escrow
  - `-` — VERDERA COMMON SHARE RESTRICTION 2
  - `-` — VERDERA ENERGY CORP. CONTRA RESTRI
  - `TSE` — TRINSEO PLC Prvt
  - `-` — COHERUS ONCOLOGY INC
  - `-` — OMNIAB INC $12.50 VESTING Prvt
  - `-` — OMNIAB INC $15.00 VESTING Prvt
  - `-` — RENT THE RUNWAY INC

### russell3000_proxy_20261015.txt

- Source: https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/latest-holdings.csv
- Output SHA-256: `6677cb241859b97c8143b9540cc514773b793ae33779ec7d3795892e60412423`
- Excluded equity-labelled rows:
  - `INH` — INHIBRX INC CVR
  - `ADRO` — CHINOOK THERAPEUTICS INC CVR
  - `ADRO` — CHINOOK THERAPEUTICS INC
  - `P5N994` — Petrocorp Inc Escrow
  - `-` — VERDERA ENERGY CORP. CONTRA RESTRI
  - `-` — VERDERA COMMON SHARE RESTRICTION 2
  - `-` — COHERUS ONCOLOGY INC
  - `HOLX` — HOLOGIC INC
  - `-` — ARCELLX INC CVR
  - `VUECF` — VERDERA ENERGY CORP
  - `-` — OMNIAB INC $12.50 VESTING Prvt
  - `-` — OMNIAB INC $15.00 VESTING Prvt
  - `UWMC-R-W` — UWM HOLDINGS RIGHTS WHEN ISSUED EX

## Local ingestion

Requires macOS/Linux and Python 3.10+. From the Jimmy repository root:

```sh
venv/bin/python -m pip install -r batch_scripts/universe_stock_metadata_ingestion/requirements.txt
venv/bin/python batch_scripts/universe_stock_metadata_ingestion/universe_stock_ingest.py --validate-inputs
```

Start with S&P 500 and groups of 10:

```sh
venv/bin/python batch_scripts/universe_stock_metadata_ingestion/universe_stock_ingest.py \
  --profile siby_baskt --universe SP500 --batch-size 10
```

Run the same command again to resume. Repeat `--universe` for other universes;
omitting it selects all five. `--limit` processes the first N sorted unique
symbols, but coverage still reports the full input universe size.

### Profile batching and subscription access

The script tries up to 10 comma-separated symbols on `/stable/profile?symbol=...`
and validates each returned symbol. This is capability detection, not a claim
that Basic supports profile batching. FMP documents its stable profile API for
one symbol and a separate profile-bulk endpoint for bulk CSV downloads. The
batch quote and market-cap endpoints do not supply the full agreed company
metadata schema, so the script does not substitute them for company profiles.

If the multi-symbol request is rejected with HTTP 400/402/403/404/422, or returns
only some requested symbols, valid returned profiles are reused and missing
symbols are fetched individually. This fallback is remembered in the checkpoint
so subsequent batches/runs do not repeatedly probe unsupported batching. Use
`--single-profile-requests` to bypass the probe entirely. Authentication errors,
malformed responses, unexpected/duplicate symbols, and single-profile access
errors stop the run without declaring the affected symbols unresolved.

A supported batch of 10 is one HTTP attempt. A rejected probe plus ten single
requests is eleven attempts. Batching does not grant access to paid endpoints.
Live batch access must be verified with your subscription; the automated tests
use mocked responses and consume no provider quota.

References: [company profile documentation](https://site.financialmodelingprep.com/developer/docs),
[profile bulk](https://site.financialmodelingprep.com/developer/docs/stable/profile-bulk),
[batch market cap](https://site.financialmodelingprep.com/developer/docs/stable/batch-market-cap).

### Progress, caching, and daily allowance

- Default group size: 10 symbols, adjustable from 1 to 10 with `--batch-size`.
- Request pacing remains 60 HTTP attempts/minute (`--requests-per-minute`).
- Default local budget: **200 HTTP attempts in a rolling 24-hour window**
  (`--max-requests-per-day`), leaving headroom under FMP's advertised 250/day Basic
  allowance. Retries, failed requests, and capability probes count too.
- The budget persists across runs in the checkpoint. It counts this script's
  attempts with that checkpoint, not other clients or historical calls made before
  the checkpoint existed. FMP remains the authority on actual quota/entitlements.
- HTTP 429 stops immediately instead of repeatedly consuming requests. Exit code
  **2** means paused; rerun after the provider allowance becomes available.
- Each validated profile is cached immediately. At the end of each group, even
  if a later request fails, successful profiles and their memberships are upserted
  into DynamoDB. Earlier groups remain uploaded when later groups fail.
- A record is marked published only after both its metadata and membership writes
  finish. Interrupted writes are replayed from the cached profile without another
  FMP request. Writes across tables are not atomic.
- Each universe reports uploaded/total, cached, skipped, and pending counts. A
  universe is logged as done only after all input symbols are uploaded or skipped.
  These counts reflect this checkpoint's acknowledgements, not a fresh database
  recount; restore/rebuild operations on DynamoDB require reconciling the cache.

The default checkpoint is `.ingestion_state/checkpoint.json` beside the script.
It stores validated metadata, completed memberships, unresolved symbols, batch
capability, and request timestamps. It contains no API key and is gitignored.
Atomic replacement protects saves; a file lock prevents concurrent runs using
that checkpoint. Keep using the same checkpoint across universes and dry runs.
Do not delete it to reset a provider allowance: that loses both resume information
and the local budget history. `--checkpoint` can override its location; separate
checkpoints do not share quota accounting. Cached metadata is reused until
explicitly refreshed; this is an initial ingestion/resume tool, not a scheduled
metadata refresh service.

Unresolved symbols are excluded from new writes to both tables and reported in
`unresolved_symbols.json` (override with `--unresolved-report`). Reruns skip them
without another FMP call unless `--retry-unresolved` is supplied. Old profiles or
memberships already in DynamoDB are not deleted. Previously known memberships
are preserved when a cached symbol is added to another selected universe.
Russell memberships retain their `_PROXY` labels.

`--dry-run` fetches and caches missing profiles and spends the same request budget,
but never connects to DynamoDB or marks profiles published. A subsequent normal
run can upload the cache without fetching those profiles again. Use `--limit 10`
for a small dry run. `--validate-inputs` is fully offline and does not change the
checkpoint or spend quota. Exit code 0 means the selected work completed, 1 means
failure, and 130 means interrupted. A limited run can complete while full-universe
coverage still has pending symbols.

### Credentials

The script resolves the FMP key at runtime from Secrets Manager secret
`jimmy-dev-fmp-api-key` in `us-east-1`, account `499133675835`. The secret contains
the raw API key as SecretString. `--fmp-secret-id` overrides the secret name/ARN.
An explicitly injected `FMP_API_KEY` environment variable takes precedence; the
script does not load `.env`. Keys are never logged or stored in the checkpoint,
reports, or DynamoDB. Cached-only resumes do not need a secret lookup.

The local profile needs `secretsmanager:GetSecretValue` on that secret,
`sts:GetCallerIdentity`, `dynamodb:DescribeTable`, and `dynamodb:BatchWriteItem`
on the two stock tables. This script does not change IAM or provision resources.

### Offline tests

With pytest installed in the project venv:

```sh
venv/bin/python -m pytest batch_scripts/universe_stock_metadata_ingestion/tests -q
```

Tests mock FMP, credentials, and DynamoDB writes. They cover complete and partial
batches, access denial fallback, persistent quota limits, HTTP 429, retries,
resumption after partial writes, dry-run caching, and overlapping universes.
