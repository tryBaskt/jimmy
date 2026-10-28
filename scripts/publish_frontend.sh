#!/usr/bin/env bash
set -euo pipefail
python3 scripts/frontend_config.py .deployment/infrastructure.json frontend/dist/config.json
bucket=$(python3 scripts/release_components.py output frontend_bucket)
distribution=$(python3 scripts/release_components.py output distribution_id)
aws s3 sync frontend/dist/ "s3://${bucket}/" --exclude index.html --exclude config.json --cache-control 'public,max-age=31536000,immutable'
aws s3 cp frontend/dist/index.html "s3://${bucket}/index.html" --cache-control 'no-store' --content-type text/html
aws s3 cp frontend/dist/config.json "s3://${bucket}/config.json" --cache-control 'no-store' --content-type application/json
invalidation=$(aws cloudfront create-invalidation --distribution-id "$distribution" --paths '/*' --query Invalidation.Id --output text)
aws cloudfront wait invalidation-completed --distribution-id "$distribution" --id "$invalidation"
