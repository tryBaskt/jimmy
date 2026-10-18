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

Install the dependencies into the project virtual environment:

```sh
venv/bin/python -m pip install -r batch_scripts/universe_stock_metadata_ingestion/requirements.txt
```

Run from the Jimmy repository root:

```sh
venv/bin/python batch_scripts/universe_stock_metadata_ingestion/universe_stock_ingest.py --validate-inputs
venv/bin/python batch_scripts/universe_stock_metadata_ingestion/universe_stock_ingest.py --profile siby_baskt --dry-run --limit 5
venv/bin/python batch_scripts/universe_stock_metadata_ingestion/universe_stock_ingest.py --profile siby_baskt
```

The script reads the FMP key at runtime from Secrets Manager secret
`jimmy-dev-fmp-api-key` in `us-east-1`, in account `499133675835`.
Use `--fmp-secret-id` to override the secret name/ARN. The secret contains the raw
API key as SecretString. The script does not load `.env`. An explicitly supplied
`FMP_API_KEY` environment variable takes precedence, for runtime secret injection.
The key is never logged or written to DynamoDB. The original `.env` is gitignored
and is not modified by this change.

Your local AWS profile needs `secretsmanager:GetSecretValue` on that exact secret,
`sts:GetCallerIdentity`, and `dynamodb:DescribeTable` plus
`dynamodb:BatchWriteItem` on both stock tables. The deployment role has only
secret metadata management permissions; it is not granted secret-value reads.

`--validate-inputs` uses no network. `--dry-run` reads the secret and fetches FMP
profiles without touching DynamoDB. `--limit` selects the first N sorted unique
symbols. `--universe` can be repeated to select specific universes.

One request is made per unique ticker. The default rate is 60 requests/minute;
set `--requests-per-minute` within the limits of your FMP subscription. Transient
requests and unprocessed DynamoDB writes are retried with bounded backoff.

All profiles are fetched and validated before any database writes. Missing or
invalid FMP profiles stop the run instead of publishing an incomplete snapshot.
The script upserts stock profiles and universe/symbol pairs, then logs when each
universe's membership writes finish. Russell universes retain `_PROXY` labels.
Writes across tables are not atomic, and old memberships are not deleted. Rerun
after resolving a failure to complete an interrupted run. This script does not
create tables or change IAM permissions.
