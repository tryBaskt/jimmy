# Historical stock ingestion

The pipeline prepares three DynamoDB tables linked by a stable security ID:

| Table | Keys | Records |
|---|---|---|
| `jimmy-dev-stock-metadata` | `security_id` | Company identity, verified dated ticker aliases, source-label observations, and unresolved identity candidates |
| `jimmy-dev-stock-universe-membership` | `universe`, `membership_key` | One observed membership interval per security/universe |
| `jimmy-dev-stock-gics-history` | `security_id`, `classification_key` | Separate GICS snapshots for each evidenced classification change |

A new classification creates a new GICS row, preserving earlier classifications.
Repeated observations of the same state extend its observed coverage. A change
back to a prior state creates another row. Keys distinguish effective-dated
history (`effective#date#state`) from observation-only data
(`observed#timestamp#state`). An observation date never becomes a historical
effective date. Source dates and references remain on each classification.

## Source and identity limitations

The supplied CSV has 2,720 dated constituent snapshots from 1996-01-02 through
2026-08-18 and 1,209 distinct source symbols. Snapshot gaps reach 91 days;
these dates are observations, not confirmed official addition/removal dates.
Some source labels appear retrospectively rewritten to successor ticker names.
The first and last observation are censored dataset boundaries. Snapshot
intervals cannot prove there were no unobserved changes between observations.

The initial parser assigns deterministic provisional IDs per contiguous source
symbol episode. Ticker reuse and separate share classes must not be silently
combined. A sourced identity registry can confirm ownership and link aliases
while retaining IDs on later updates. Company-name and current ticker matches
alone do not prove that a historical episode belongs to that company.

Bigdata's Knowledge Graph supplies vendor classifications, which are retained as
vendor labels rather than renamed GICS. Grounded research requests actual GICS
classification changes, effective dates, ticker changes, and company identity
evidence. Ambiguous matches stay explicitly unresolved. Missing historical
classifications remain unknown. Only allowlisted classification and factual
identity metadata are persisted; underlying documents, snippets, and generated
research reports are discarded.

## Prepare and enrich

Run from the repository root using the existing Python environment:

```sh
venv/bin/python batch_scripts/historical_stock_ingestion/snapshot_history.py \
  --csv '/path/to/S&P 500 Historical Components & Changes (Updated).csv' \
  --output batch_scripts/historical_stock_ingestion/artifacts/base-plan.json

venv/bin/python batch_scripts/historical_stock_ingestion/bigdata_enrichment.py \
  --csv '/path/to/S&P 500 Historical Components & Changes (Updated).csv' \
  --output batch_scripts/historical_stock_ingestion/artifacts/bigdata_enrichment.json \
  --kg --research --research-batches 4 --credit-budget 4000

venv/bin/python batch_scripts/historical_stock_ingestion/reconcile.py \
  --plan batch_scripts/historical_stock_ingestion/artifacts/base-plan.json \
  --enrichment batch_scripts/historical_stock_ingestion/artifacts/bigdata_enrichment.json \
  --output batch_scripts/historical_stock_ingestion/artifacts/enriched-plan.json
```

`BIGDATA_DEV_API_KEY` is read at runtime from the environment or the root `.env`.
Its value is never logged. The enrichment checkpoint resumes completed symbol
lookups and research batches. Inspect its coverage and rejection report; a
completed request does not imply complete historical data coverage. Continue
using the previously enriched plan on subsequent runs to preserve prior GICS
snapshots. Artifacts are ignored by Git.

## Authorized replacement and publication

Back up the original tables before changing their primary keys. Table creation
and replacement belong to the Terraform root under `infra/dev`; the publisher
cannot delete tables. Review its saved plan for the exact three stock tables.
Deploy the compatible reader and scoped metadata BatchGetItem permission before
replacing the membership schema. The normal deployment path is the dev workflow;
a directly authorized migration must preserve the same state and resource scope.

```sh
venv/bin/python batch_scripts/historical_stock_ingestion/publish.py backup \
  batch_scripts/historical_stock_ingestion/artifacts/pre-migration-backup.json
venv/bin/python batch_scripts/historical_stock_ingestion/publish.py validate \
  batch_scripts/historical_stock_ingestion/artifacts/enriched-plan.json
venv/bin/python batch_scripts/historical_stock_ingestion/publish.py publish \
  batch_scripts/historical_stock_ingestion/artifacts/enriched-plan.json
venv/bin/python batch_scripts/historical_stock_ingestion/publish.py verify \
  batch_scripts/historical_stock_ingestion/artifacts/enriched-plan.json
```

AWS access defaults to profile `siby_baskt`, account `499133675835`, and region
`us-east-1`; a different account is rejected. Publication checks table schemas
and every cross-table security ID. Batch writes retry unprocessed rows and can
resume. Verification performs strongly consistent paginated reads and compares
all stored records with the plan, including unexpected rows. The backup retains
original key schemas and exact Decimal values, and refuses overwriting a prior
backup. To recover a failed replacement, recreate the original schemas through
Terraform and restore the corresponding backup items before restoring the old
reader/task definition.

The application reader uses the latest dataset observation by default. Explicit
historical ticker resolution requires verified dated aliases. The strategy
workflow still uses one universe per run; per-rebalance historical membership
selection is a separate integration step.

## Verification

```sh
venv/bin/python -m pytest batch_scripts/historical_stock_ingestion/tests \
  packages/agents/strategy_agent/tests/test_dynamodb.py -q
```

### Public MSCI classification-change supplement

The public MSCI announcement search form exposes an **All** date-range option.
`msci_public.py` submits that exact form and follows its published GICS notice links:

```sh
python -m batch_scripts.historical_stock_ingestion.msci_public \
  --output batch_scripts/historical_stock_ingestion/artifacts/msci-public-gics-facts.json
```

It persists only company names, country, old/new classification facts, publication
and effective dates, and source URLs. Original announcement text is not saved.
Errors and amendment flags require review; no name-to-security joins are inferred.
`effective_on` has `effective_timing=market_close`, which must be preserved when
converting to table intervals. Do not treat that date as effective at its opening.

The public Standard archive currently reaches June 2002 for GICS notices. This
is a supplement, not complete company history: the notices explicitly omit changes
confined to sub-industry, and corporate events and structural reviews may require
separate sources. An old classification at a change date does not prove that same
classification held since the beginning of a security's membership history.
