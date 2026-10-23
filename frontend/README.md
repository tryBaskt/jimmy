# Jimmy frontend

The React site is built by the dev GitHub Actions workflow and deployed to
private S3 behind CloudFront. CloudFront forwards `/api/*` to API Gateway. The
deployed site uses Cognito sign-in and submits asynchronous strategy jobs; it
cannot execute jobs in the browser.

Local commands check and build source code only:

```sh
npm ci
npm run lint
npm run build
```
