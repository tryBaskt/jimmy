# Shared Python packages

Put reusable code here when more than one running program needs it. Keep HTTP
handling and worker lifecycle code in their respective deployables, with each
build installing only the packages it needs. `clients/` contains the reusable
DynamoDB, Yahoo Finance, and VectorBT adapters.
