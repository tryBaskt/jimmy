# Paper replication service

This service owns its HTTP API, LangGraph workflow and research models.
`src/paper_replication_api.py` exposes `/api/paper-replication/*`; it uses shared
auth/transport helpers from `packages` and imports no other workflow. `runtime.py` exposes the AgentCore HTTP protocol through the official
SDK (`/invocations` and `/ping`, port 8080).

`start` registers an AgentCore asynchronous task, starts the graph in a child process,
and returns a run snapshot immediately. The SDK reports the session as busy while the
child executes. `snapshot`, `artifact` and `series` read the same session; `cancel`
terminates the child. Duplicate starts for the same session/run return the existing run.
Shutdown terminates active children. There is no detached work after cancellation.

The API sets the authenticated owner. Every action checks it before reading artifacts.
Runtime access in AWS uses IAM/SigV4 and is granted to the API role. Container images
are built for Linux ARM64. The runtime role can pull its image, write its logs, and
read the configured OpenAI key secret; it has no database or artifact-bucket permissions.
Model inference uses OpenAI directly, while AgentCore provides hosting.

`workflow.py` is the child process entry point and defines the LangGraph workflow.
It calls LangChain's `create_agent` directly for Principal Investigator decisions
and for each Research Assistant assignment.

1. The PI extracts page-grounded claims, plans the paper tests and exploratory
   extrapolations, and requests data based on the experiments and their warmup needs.
2. Research Assistants collect up to four distinct requests in parallel per round,
   using Yahoo adjusted prices or validated public monthly-return CSVs. The PI
   reviews the observed coverage, selects a candidate, or requests alternatives.
   There are at most four rounds, and candidates from earlier rounds remain eligible.
3. If needed, the PI revises the planned experiments to fit collected data. The graph
   validates the revised coverage and freezes the executable suite before backtesting.
4. Research Assistants run the assigned backtests in parallel. Each sees one tool
   bound to its assignment. The PI reviews the completed results and writes the report.

The Yahoo Finance and VectorBT adapters live in `packages/tools`. This service
owns paper-specific data validation, numerical methods, and report formatting.

Supported templates are monthly cross-sectional momentum, time-series momentum and
buy-and-hold. Tests cover signal timing, cohort construction, missing observations,
transaction costs, HAC metrics, VectorBT account diagnostics, and graph branch joins.
Unsupported paper methods and failed acquisitions remain visible. Adjusted Yahoo prices
and simplified historical membership cannot establish an exact replication. Exploratory
tests are not untouched holdouts; multiplicity corrections and arbitrary author code are
not implemented.

`workflow.py` uses OpenAI Responses with explicit output-token limits. Structured model
outputs go through a Pydantic schema and citation checks. Web search is disabled until a
real provider is configured; the service does not fabricate discovery results.

All files are session-local under `/tmp/jimmy`. There are no durable checkpoints, catalog,
shared cache or resumable jobs. Download the report before the session expires.

Protocol references:

- [AgentCore asynchronous tasks](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)
- [InvokeAgentRuntime](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html)

The service has two images: `Dockerfile.api` runs its Lambda HTTP adapter and
`Dockerfile` runs the graph on AgentCore. The registry and shared Terraform module
deploy both together. API dependencies are in the base package; runtime/scientific
dependencies are in the `runtime` extra.

The runtime runs on AgentCore and resolves `OPENAI_API_KEY_SECRET_ARN` from Secrets
Manager. `OPENAI_MODEL` defaults to `gpt-6.1-sol`. The API uses Cognito authentication
and IAM-signed AgentCore requests. `RESEARCH_ASSISTANT_MODEL` defaults to `gpt-5-mini`.
There is no local application mode.

All Python modules live directly in `src/`. The AgentCore server starts with
`python -m runtime`; it starts graph execution with `python -m workflow`.
