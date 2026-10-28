#!/usr/bin/env bash
set -euo pipefail
: "${DEPLOY_ENV:?environment required}" "${AWS_ACCOUNT_ID:?account required}" "${AWS_REGION:?region required}" "${GITHUB_SHA:?commit required}"
target="${1:?api or worker required}"
[[ "$DEPLOY_ENV" == dev || "$DEPLOY_ENV" == prod ]]
[[ "$target" == api || "$target" == worker ]]
mkdir -p .deployment
registry="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
tag="${GITHUB_SHA}-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
repository="jimmy-${DEPLOY_ENV}-${target}"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$registry"
docker buildx build --platform linux/amd64 --provenance=false --target "$target" --tag "${registry}/${repository}:${tag}" --push .
digest=$(aws ecr describe-images --repository-name "$repository" --image-ids "imageTag=${tag}" --query 'imageDetails[0].imageDigest' --output text)
python3 - "$target" "${registry}/${repository}@${digest}" <<'PY'
import json, os, pathlib, sys
target, image = sys.argv[1:]
pathlib.Path(f'.deployment/{target}-image.json').write_text(json.dumps({'image': image, 'commit': os.environ['GITHUB_SHA'], 'environment': os.environ['DEPLOY_ENV']}))
PY
