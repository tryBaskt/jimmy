# Adding a workflow

A workflow is an independently deployable service under `services/<id>/`. It owns
its HTTP interface, input/output schemas, execution graph and tests. API Gateway
routes `/api/<id-with-dashes>/*` to that service's API Lambda. AgentCore hosts its
graph in a separate runtime. The adapter handles HTTP uploads/downloads and signs
runtime calls; it is deployed with its workflow, not as a central API application.

## Create and register

Create a service directory with the following files (using `document_review` as an example):

```text
services/document_review/
  src/document_review_api.py
  src/document_review_runtime.py
  tests/test_http.py
  Dockerfile
  Dockerfile.api
  pyproject.toml
  README.md
```

Add an entry to the `workflows` array in `workflows.json`:

```json
{
  "id": "document_review",
  "name": "Document review",
  "api_prefix": "/api/document-review",
  "requires_openai": false
}
```

IDs use lowercase letters, numbers and underscores, start with a letter, and have
at most 32 characters. API prefixes must match their IDs. Registry validation rejects
duplicates, incomplete services and invalid IDs before provisioning.

Set `requires_openai: true` if it invokes OpenAI; the environment's model and secret
ARN then reach the runtime, whose role can read that secret. Add the OpenAI SDK to
the service's runtime dependencies and resolve its key before constructing the client;
paper replication's `settings.py` demonstrates runtime secret loading in AWS.

## Shared conventions

- Export a FastAPI `app` and Lambda `handler` from `<id>_api.py`.
- Use `http_utils.app.create_app(router, workflow_id=..., title=...)`. It adds CORS,
  public `/<prefix>/health`, authenticated `/<prefix>/me`, and authentication on all
  business routes. Use `Depends(security.workflow_auth.current_user)` when a handler needs the user ID.
- Call `clients.agentcore_client.invoke(session_id, owner, action, ...)` from the workflow API.
  It forwards the verified user ID and uses the workflow's scoped IAM role. Never accept
  an owner from an HTTP request body in place of the authenticated identity.
- Expose the AgentCore HTTP protocol through `BedrockAgentCoreApp`. A synchronous
  starter should finish within the HTTP request timeout. For longer work, register
  an async task and respond immediately; paper replication demonstrates isolated
  execution, status checks, cancellation, ownership checks and shutdown handling.
- Store runtime-only dependencies in the `runtime` extra. The API image installs the
  base service and shared HTTP package, not scientific/runtime dependencies.
- Build both images from the repository root. CI uses `Dockerfile` for the runtime
  and `Dockerfile.api` for Lambda, with Linux ARM64.

The shared package contains authentication, HTTP setup and AgentCore transport. Workflow-specific business logic belongs in the service.

## Test

```bash
python -m pip install -e 'services/document_review[runtime]' pytest
python -m pytest services/document_review/tests
```

Add a frontend view/client for the workflow's specific UX.
The frontend's published configuration includes the workflow registry for discovery.
The existing paper UI intentionally calls only the paper replication prefix.

## Deployment behavior

No workflow-specific GitHub job or Terraform root is required. The registry drives:

1. CI test matrices, installing each service independently.
2. ECR API/runtime repositories in the foundation stack.
3. A separate reusable deployment invocation for each workflow, with image-publishing
   and provisioning jobs.
4. The reusable `infra/modules/workflow` module, called through `infra/workflows`.
5. State isolation at `workflows/<id>/terraform.tfstate` in the environment's state bucket.
6. Frontend post-deployment health/authentication checks for every workflow.

Dev/prod branch gates and OIDC authentication remain unchanged. A failed workflow
blocks frontend publication; matrix siblings continue so failures stay independently
visible. Shared auth is provisioned before workflow deployment. Removing a registry
entry is not a decommission procedure: retire its state/resources deliberately first.

If you deployed the previous central API layout, migrate or retire those resources
and their state before using the new module layout. The paper API URLs have changed
from `/api/runs` to `/api/paper-replication/runs`; clients are updated in this refactor.
