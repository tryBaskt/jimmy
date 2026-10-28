# Implementation status

This PR implements the ground-up application and deployable infrastructure. The architecture document remains the target contract; the following boundaries prevent unsupported behavior from being mistaken for completed functionality.

| Capability | Status |
| --- | --- |
| Main claim and experiment-suite planning, page-grounded citations | Implemented through Kimi K3 on Bedrock for all AI roles; actual model evaluation requires enabled account access |
| Automatic planner-owned data validation and bounded substitute/replan loop | Implemented; connector and method capabilities constrain what can execute |
| Price and document connectors | yfinance monthly adjusted-return derivation; Bigdata document search; private CSV numerical inputs |
| Future connector onboarding | Protocol and registry; arbitrary external code/URLs are not accepted |
| Catalog-first exact-request reuse, byte deduplication, acquisition lease fencing | Implemented with scoped reuse; cross-user Yahoo reuse opt-in |
| Compatible-superset catalog matching | Implemented with explicit slice filters and entitlement/semantic matching |
| Partial-range acquisition and automated reference garbage collection | Planned; immutable snapshots and retained references are kept without automatic deletion |
| Paper experiments, modifications/caveats, exactly two result groups | Implemented |
| Isolated trusted numerical execution | Implemented with separate Fargate role/network for numerical stages |
| Monthly overlapping momentum sorts and return-series metrics | Implemented with numerical fixtures; before costs; historical membership limitations remain explicit |
| Broad author-code/generated-code execution | Disabled; trusted templates only |
| PDF extraction | Searchable PDF extraction plus bounded AWS Textract fallback for scanned/sparse PDFs; cloud behavior needs deployment verification |
| Extrapolations through the common plan/data/freeze/build/run/evaluate pipeline | Implemented; one bounded exploratory generation; no untouched-holdout claim |
| Statistical multiplicity adjustments and arbitrary model training protocols | Not implemented; report does not claim those validations |
| Live plans/processes/results and durable event replay | Implemented; socket notifications with cursor replay/polling fallback |
| API Gateway service routing | Separate service routers and Lambda instances behind one HTTP gateway; shared metadata table initially |
| New AWS resources and separate dev/prod Actions | Bootstrap applied and read back on 2026-10-28: deployment roles/secret-write policies, immutable image repositories and private versioned state buckets; follow-up plan shows no changes. Dev deployment completed on 2026-10-28: infrastructure, component releases, frontend publishing and public HTTP/config/API checks passed; new frontend bucket avoids the legacy app's bucket |
| Deployment secret provisioning | GitHub environment secrets for account/role, Bigdata API key and alert email; AWS Secrets Manager sync for key/email without plaintext Terraform state; SNS email configured separately after deployment |
| Component deployment jobs | Separate secret/build/infrastructure/API/realtime/task/workflow/frontend/alerts/verification jobs; one Terraform writer, commit-bound artifacts and pinned task revisions |
| Cognito browser sign-in | Verified against the live dev workspace after adding explicit unauthenticated preflight routes; actual API methods remain protected. Callback/session and CORS smoke regression checks are included |
| Generic paper-quality score or proof of correctness | Deliberately absent |

The dev workspace is available at https://d2bv0tm4acl85c.cloudfront.net/. Deployment-role fixes include unscoped definition validation and publisher/environment-scoped event-source mapping permissions, applied and verified in AWS. Cognito account invitations and model/provider end-to-end research evaluation remain separate from the successful public deployment checks.

Remaining production-hardening work should be tracked separately: provider entitlements, a human-scored extraction benchmark, account-specific model invocation smoke tests, broader scientific templates, verification of cancellation/failure reconciliation under real ECS failures, subscription TTL cleanup and data lifecycle automation.
