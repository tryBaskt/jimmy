# Jimmy

Workflow-oriented research applications run as on-demand Amazon ECS Fargate tasks using OpenAI models. Each workflow
owns its HTTP API and execution code. API Gateway routes to the workflow's own
Lambda adapter, which starts a standalone Fargate task for each submitted paper.

```text
workflows.json                       Registry used by CI, deployment and infrastructure
services/paper_replication/
  src/paper_replication_api.py        Workflow-owned HTTP endpoints
  src/harness/workflow.py                    LangChain Principal Investigator harness
  Dockerfile.api                     API Lambda image
  Dockerfile                         Fargate task image
packages/                            Shared authentication and API setup
infra/foundation/                    Gateway, hosting, repositories and shared log encryption
infra/auth/                          Cognito and JWT authorizer
infra/modules/workflow/              Reusable API + ECS deployment module
infra/workflows/                     Module entry point, separate state per workflow
.github/workflows/                   Registry-driven CI and deployment matrices
.github/actions/                     Reusable deployment steps
```

There is no central API service or central Python router to update. Shared helpers
contain infrastructure concerns; paper-specific routes, schemas and agent tools stay
inside the paper replication service.

## Add a workflow

Create `services/<id>/` with its API, Fargate task entry point, dependencies, Dockerfiles
and tests. Add an entry to `workflows.json`; CI and deployment discover it automatically.
Add frontend views as needed.

See [adding a workflow](docs/adding-a-workflow.md) for the ownership and execution contract.

## Paper replication

One LangChain Principal Investigator plans paper experiments and extrapolations
from page-grounded claims. It delegates data collection to Research Assistants
in parallel across up to four rounds, reviews coverage, and freezes the experiment
suite. Assistants run the frozen suite in parallel using trusted numerical
templates, then the PI writes its assessment from the resulting evidence.

The frontend calls `/api/paper-replication/*`. Cognito provides email signup and sign-in, API Gateway
checks JWTs, and the workflow API checks ownership on every run and artifact request. The
API stores the input in S3 and starts the Fargate task. S3 stores progress
and the report after the task exits. Run discovery is browser-local.
Searchable PDFs up to 3 MB / 250 pages are supported; scanned papers need OCR first. Web search
is disabled until a real provider is configured.

## Verification

The application runs in AWS: CloudFront serves the frontend, API Gateway routes to
Lambda, Cognito authenticates users, and ECS Fargate runs each workflow. OpenAI
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
secrets. Terraform creates the OpenAI secret and passes its ARN to the ECS task automatically.
Dev has the ECS deployment permissions. See [deployment setup](docs/deployment.md).
The runtime loads its key from Secrets Manager;
it does not read `.env` files. `OPENAI_MODEL` defaults to `gpt-6.1-sol` for the PI,
and `RESEARCH_ASSISTANT_MODEL` defaults to `gpt-5-mini` for the assistants.
`PI_MAX_MODEL_CALLS` defaults to 48 per run.

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
