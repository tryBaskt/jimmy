#!/usr/bin/env bash
set -euo pipefail
: "${DEPLOY_ENV:?environment required}" "${AWS_ACCOUNT_ID:?account required}" "${AWS_REGION:?region required}" "${GITHUB_SHA:?commit required}"
[[ "$DEPLOY_ENV" == dev || "$DEPLOY_ENV" == prod ]]
# This file contains only validated ARN references emitted by deployment_secrets.py.
set -a
source .deployment/secret-references.env
set +a
export TF_VAR_environment="$DEPLOY_ENV" TF_VAR_region="$AWS_REGION"
export TF_VAR_api_image
export TF_VAR_worker_image
TF_VAR_api_image=$(python3 scripts/release_components.py image api)
TF_VAR_worker_image=$(python3 scripts/release_components.py image worker)
export TF_VAR_bigdata_secret_arn="${BIGDATA_SECRET_ARN:-}"
export TF_VAR_yfinance_shared_cache="${YFINANCE_SHARED_CACHE:-false}"
terraform -chdir=infra/environments init -input=false -backend-config="bucket=jimmy-${DEPLOY_ENV}-state-${AWS_ACCOUNT_ID}" -backend-config="key=application.tfstate" -backend-config="region=${AWS_REGION}" -backend-config="use_lockfile=true"
terraform -chdir=infra/environments plan -input=false -out=deploy.tfplan
terraform -chdir=infra/environments apply -input=false deploy.tfplan
terraform -chdir=infra/environments output -json > .deployment/infrastructure.json
python3 - <<'PY'
import json, os, pathlib
path = pathlib.Path('.deployment/infrastructure.json')
data = json.loads(path.read_text())
data['_release'] = {'commit': os.environ['GITHUB_SHA'], 'environment': os.environ['DEPLOY_ENV']}
path.write_text(json.dumps(data))
PY
