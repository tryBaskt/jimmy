# Paper replication service

API Gateway routes to `paper_replication_api.py` on Lambda. `jobs.py` starts
one standalone ECS Fargate task per paper and handles polling and cancellation.
The task runs `python -m runtime`, loads the upload from S3, and invokes the
harness. It publishes progress, reports and experiment series to S3 and exits.

The API scopes run keys to the authenticated Cognito identity. The S3 bucket
blocks public access, and the task accepts only a run key from Lambda.

`harness/workflow.py` runs one LangChain
`create_agent` invocation for the PI. The PI calls validated tools to
save its plan, delegate work, and freeze the experiment suite. A tool returns
the result of each assistant batch to the same PI conversation.

1. The PI extracts page-grounded claims, plans the paper tests and exploratory
   extrapolations, and requests data based on the experiments and their warmup needs.
2. Research Assistants collect up to four distinct requests in parallel per round,
   using Yahoo adjusted prices or validated public monthly-return CSVs. The PI
   reviews the observed coverage, selects a candidate, or requests alternatives.
   There are at most four rounds, and candidates from earlier rounds remain eligible.
3. If needed, the PI revises the planned experiments to fit collected data. The harness
   validates the revised coverage and freezes the executable suite before backtesting.
4. Research Assistants run the assigned backtests in parallel. Each sees one tool
   bound to its assignment. The PI reviews the completed results and writes the report.

Market data, VectorBT backtesting, and reporting live in `src/harness/tools/`.

Supported templates are monthly cross-sectional momentum, time-series momentum and
buy-and-hold. Tests cover signal timing, cohort construction, missing observations,
transaction costs, HAC metrics, VectorBT account diagnostics, and parallel delegation.
Unsupported paper methods and failed acquisitions remain visible. Adjusted Yahoo prices
and simplified historical membership cannot establish an exact replication. Exploratory
tests are not untouched holdouts; multiplicity corrections and arbitrary author code are
not implemented.

`workflow.py` uses OpenAI Responses with explicit output-token limits. Structured model
outputs go through a Pydantic schema and citation checks. Web search is disabled until a
real provider is configured; the service does not fabricate discovery results.

The worker uses `/tmp/jimmy` for intermediate files. The API and worker share
run metadata and outputs through S3.

`Dockerfile.api` packages the Lambda adapter. `Dockerfile` packages the ARM64
Fargate worker with the service's `runtime` dependencies. The worker resolves
`OPENAI_API_KEY_SECRET_ARN` from Secrets Manager. `OPENAI_MODEL` defaults to
`gpt-6.1-sol`, `RESEARCH_ASSISTANT_MODEL` defaults to `gpt-5-mini`, and
`PI_MAX_MODEL_CALLS` defaults to 48.
