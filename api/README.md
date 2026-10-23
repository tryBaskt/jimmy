# Jimmy API

FastAPI runs in the AWS Lambda provisioned by `infra/dev/api`. API Gateway
routes `/api/*` to this Lambda, and CloudFront forwards the site's `/api/*`
requests to API Gateway. The dev GitHub Actions workflow packages and deploys it.

The API handles Cognito sign-in with an opaque browser session. Access and
refresh tokens stay in the `jimmy-dev-user-sessions` DynamoDB table. An
authenticated `POST /api/strategy/run` stores a job in DynamoDB, sends its ID
to SQS, and returns HTTP 202. The Fargate strategy worker performs the test and
writes the report to S3. The browser polls `GET /api/strategy/jobs/{job_id}`;
job reads are scoped to the signed-in user.

The API does not execute strategies or accept jobs without the AWS queue,
tables, Cognito, and results bucket configured. There is no local runtime
launcher. Tests can still run locally without AWS:

```sh
venv/bin/python -m pytest -c api/pytest.ini api/tests
```
