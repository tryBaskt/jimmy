#!/usr/bin/env bash
set -euo pipefail
: "${DEPLOY_ENV:?dev or prod required}"
: "${AWS_REGION:?region required}"
: "${AWS_ACCOUNT_ID:?account required}"
: "${GITHUB_SHA:?commit required}"
if [[ "$DEPLOY_ENV" != dev && "$DEPLOY_ENV" != prod ]]; then exit 2; fi
registry="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
# Immutable tags include the workflow attempt so a deliberate redeploy can rebuild safely.
image_tag="${GITHUB_SHA}-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$registry"
for target in api worker; do
  image="${registry}/jimmy-${DEPLOY_ENV}-${target}:${image_tag}"
  docker buildx build --platform linux/amd64 --provenance=false --target "$target" --tag "$image" --push .
done
api_digest=$(aws ecr describe-images --repository-name "jimmy-${DEPLOY_ENV}-api" --image-ids "imageTag=${image_tag}" --query 'imageDetails[0].imageDigest' --output text)
worker_digest=$(aws ecr describe-images --repository-name "jimmy-${DEPLOY_ENV}-worker" --image-ids "imageTag=${image_tag}" --query 'imageDetails[0].imageDigest' --output text)
export TF_VAR_environment="$DEPLOY_ENV" TF_VAR_region="$AWS_REGION"
export TF_VAR_api_image="${registry}/jimmy-${DEPLOY_ENV}-api@${api_digest}"
export TF_VAR_worker_image="${registry}/jimmy-${DEPLOY_ENV}-worker@${worker_digest}"
export TF_VAR_bigdata_secret_arn="${BIGDATA_SECRET_ARN:-}"
export TF_VAR_yfinance_shared_cache="${YFINANCE_SHARED_CACHE:-false}"
export TF_VAR_alert_email="${ALERT_EMAIL:-}"
terraform -chdir=infra/environments init -input=false -backend-config="bucket=jimmy-${DEPLOY_ENV}-state-${AWS_ACCOUNT_ID}" -backend-config="key=application.tfstate" -backend-config="region=${AWS_REGION}" -backend-config="use_lockfile=true"
terraform -chdir=infra/environments plan -input=false -out=deploy.tfplan
terraform -chdir=infra/environments apply -input=false deploy.tfplan
terraform -chdir=infra/environments output -json > "$RUNNER_TEMP/outputs.json"
npm --prefix frontend ci
npm --prefix frontend run build
python3 scripts/frontend_config.py "$RUNNER_TEMP/outputs.json" frontend/dist/config.json
bucket=$(terraform -chdir=infra/environments output -raw frontend_bucket)
distribution=$(terraform -chdir=infra/environments output -raw distribution_id)
aws s3 sync frontend/dist/ "s3://${bucket}/" --exclude index.html --exclude config.json --cache-control 'public,max-age=31536000,immutable'
aws s3 cp frontend/dist/index.html "s3://${bucket}/index.html" --cache-control 'no-store' --content-type text/html
aws s3 cp frontend/dist/config.json "s3://${bucket}/config.json" --cache-control 'no-store' --content-type application/json
invalidation=$(aws cloudfront create-invalidation --distribution-id "$distribution" --paths '/*' --query Invalidation.Id --output text)
aws cloudfront wait invalidation-completed --distribution-id "$distribution" --id "$invalidation"
url=$(terraform -chdir=infra/environments output -raw frontend_url)
api_url=$(terraform -chdir=infra/environments output -raw api_url)
curl --fail --retry 5 --retry-all-errors "${api_url}/api/health"
curl --fail --retry 5 --retry-all-errors "${url}/config.json"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '### Jimmy %s deployed\n\n[Open frontend](%s)\n\nAPI: `%s`\n\nCommit: `%s`\n' "$DEPLOY_ENV" "$url" "$api_url" "$GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
fi
