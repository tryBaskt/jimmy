# Jimmy

Workflow-oriented research applications hosted on Amazon Bedrock AgentCore, using OpenAI models. Each workflow
owns its HTTP API and execution code. API Gateway routes to the workflow's own
Lambda adapter, which invokes that workflow's AgentCore runtime.

```text
workflows.json                       Registry used by CI, deployment and infrastructure
services/paper_replication/
  src/paper_replication_api.py        Workflow-owned HTTP endpoints
  src/workflow.py                    LangChain agents and LangGraph orchestration
  Dockerfile.api                     API Lambda image
  Dockerfile                         AgentCore runtime image
packages/                            Shared auth, transport, app setup and research tools
infra/foundation/                    Gateway, hosting, repositories and shared log encryption
infra/auth/                          Cognito and JWT authorizer
infra/modules/workflow/              Reusable API + AgentCore deployment module
infra/workflows/                     Module entry point, separate state per workflow
.github/workflows/                   Registry-driven CI and deployment matrices
.github/actions/                     Reusable deployment steps
```

There is no central API service or central Python router to update. Shared helpers
contain infrastructure concerns; paper-specific routes, schemas and graph logic stay
inside the paper replication service.

## Add a workflow

Create `services/<id>/` with its API, AgentCore runtime, dependencies, Dockerfiles
and tests. Add an entry to `workflows.json`; CI and deployment discover it automatically.
Add frontend views as needed.

See [adding a workflow](docs/adding-a-workflow.md) for the ownership and execution contract.

## Paper replication

The Principal Investigator plans paper experiments and extrapolations from
page-grounded claims. Research Assistants collect data in parallel across up to
four rounds. The PI reviews coverage and revises experiments when needed, then
the assistants run the frozen suite in parallel using trusted numerical templates.
The PI writes the report from the resulting evidence.

The frontend calls `/api/paper-replication/*`. Cognito provides sign-in, API Gateway
checks JWTs, and the workflow API checks the user's identity and passes it to its
IAM-authenticated runtime. Every runtime artifact request checks ownership.

The persistent data layer is deferred. Run files live inside the AgentCore session
and are lost when it ends; browser-local run summaries are not durable history.
Download reports while the session is available. Sessions expire after 15 idle
minutes or the configured eight-hour maximum. Runs are not resumable. Searchable
PDFs up to 3 MB / 250 pages are supported; scanned papers need OCR first. Web search
is disabled until a real provider is configured.

## Verification

The application runs in AWS: CloudFront serves the frontend, API Gateway routes to
Lambda, Cognito authenticates users, and AgentCore hosts each workflow. OpenAI
provides model inference. There is no local application runtime or authentication bypass.

Tests and builds can run without deploying or accessing AWS. Use Python 3.12 and Node 24:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
npm --prefix apps/web ci
npm --prefix apps/web test
npm --prefix apps/web run build
```

GitHub environments provide `AWS_ACCOUNT_ID`, `AWS_REGION`, and `OPENAI_API_KEY`
secrets. Terraform creates the OpenAI secret and passes its ARN to AgentCore automatically.
The dev deployment role is bootstrapped, so merge into `dev` to deploy. The runtime loads its key from Secrets Manager;
it does not read `.env` files. `OPENAI_MODEL` defaults to `gpt-6.1-sol` for the PI,
and `RESEARCH_ASSISTANT_MODEL` defaults to `gpt-5-mini` for the assistants.

## Deployment

Pushes to `dev` and `prod` deploy to the matching GitHub environment. Protect those
branches to require reviewed merges and passing CI. PRs never deploy; there is no
manual deployment trigger.

Infrastructure is provisioned first, then Cognito. A matrix job named for each
registered workflow calls a reusable two-job deployment: publish its API/runtime
ARM64 images, then provision the workflow using state key
`workflows/<id>/terraform.tfstate`. Workflows deploy independently in parallel. Frontend publishing waits for all workflow jobs to succeed and verifies
each registered API's health/authentication.

Deployment commands live in GitHub Actions; AWS resource definitions live in Terraform.
See [deployment setup](docs/deployment.md) for required OIDC roles and GitHub settings.
No replacement resources were deployed during this refactor. Cloud login/model access
and container builds still need deployment validation; local tests do not establish them.
