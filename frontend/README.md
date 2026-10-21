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
and displays the backtest, SPY comparison, and dated positions with directions
and weights. The user chooses a 1–12 month lookback. Bedrock translates an
explicit price-based hypothesis into one supported signal, then makes a
portfolio decision from prior-close signals at each monthly or quarterly
rebalance. VectorBT replays those saved decisions with trading costs.
Supported signals are trailing return, realized volatility, drawdown, distance
from average price, and beta to SPY. Strategies needing point-in-time
fundamentals or unstructured documents are not yet available. Market-cap weights
use Yahoo's historical share-count estimates. There is no separate training/holdout split. The strategy endpoint
uses the AWS profile from the shell; no credentials belong in frontend code.

`npm run lint` checks source; `npm run build` checks TypeScript and creates `dist/`.
`npm run preview` previews static output only; it does not proxy API requests.

In dev AWS, the provisioning frontend job uploads `dist/` to private S3 and
CloudFront serves the app over HTTPS. CloudFront forwards `/api/*` to API Gateway,
so no environment-specific API URL is embedded in JavaScript.
The hosted strategy form remains disabled because the public Lambda does not
run long backtests or have research-data permissions yet.
