# Jimmy frontend

React + TypeScript with Vite. Use Node 24.

```sh
cd frontend
npm ci
npm run dev
```

To start both frontend and API together, run `AWS_PROFILE=siby_baskt npm run dev`
from the repository root (after `npm ci` there). Press Ctrl+C to stop both.

Open http://127.0.0.1:5173. When starting only the frontend, start FastAPI separately
using the API README.
Vite proxies `/api/*` to http://127.0.0.1:8000. `FRONTEND_PORT` and `API_PORT`
can override the local listening/proxy ports. The page accepts a strategy prompt
and displays the monthly backtest, SPY comparison, and stock selections.
The user chooses a 1–12 month lookback for each month's stock ranking. Bedrock
translates the prompt into validated price signals: trailing return, realized
volatility, drawdown, distance from average price, or beta to SPY, each ranked
highest or lowest. Up to three signals can be combined. Fundamental value
strategies need historical point-in-time fundamental data and are not yet
available. There is no separate training/holdout split. The research endpoint is enabled by the root local
launcher and uses the AWS profile from the shell; no credentials belong in
frontend code. Currently it supports monthly, equal-weight S&P 500 momentum.

`npm run lint` checks source; `npm run build` checks TypeScript and creates `dist/`.
`npm run preview` previews static output only; it does not proxy API requests.

In dev AWS, the provisioning frontend job uploads `dist/` to private S3 and
CloudFront serves the app over HTTPS. CloudFront forwards `/api/*` to API Gateway,
so no environment-specific API URL is embedded in JavaScript.
The hosted research form remains disabled because the public Lambda does not
run long backtests or have research-data permissions yet.
