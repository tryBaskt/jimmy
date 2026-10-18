# Workers

Workers run jobs outside the HTTP request lifecycle. Each worker owns its entry
point, runtime dependencies, and deployment package. Shared data contracts and
calculations belong in `packages/` when another program also needs them.

`strategy_research` is a locally runnable finite job. It has no deployment
configuration yet; an on-demand Fargate task remains a possible future runtime.
