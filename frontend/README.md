# Jimmy frontend

React + TypeScript with Vite. Use Node 24.

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Start FastAPI separately using the backend README.
Vite proxies `/api/*` to http://127.0.0.1:8000. The starter page checks backend
liveness and reports connection errors. No credentials belong in frontend code.

`npm run lint` checks source; `npm run build` checks TypeScript and creates `dist/`.
`npm run preview` previews static output only; it does not proxy API requests.

In dev AWS, the provisioning frontend job uploads `dist/` to private S3 and
CloudFront serves the app over HTTPS. CloudFront forwards `/api/*` to API Gateway,
so no environment-specific backend URL is embedded in JavaScript.
