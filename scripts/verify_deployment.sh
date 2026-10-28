#!/usr/bin/env bash
set -euo pipefail
url=$(python3 scripts/release_components.py output frontend_url)
api_url=$(python3 scripts/release_components.py output api_url)
python3 scripts/verify_cors.py --api-url "$api_url" --origin "$url"
curl --fail --retry 5 --retry-all-errors "${api_url}/api/health"
curl --fail --retry 5 --retry-all-errors "${url}/config.json"
curl --fail --retry 5 --retry-all-errors --output /dev/null "${url}/"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '### Jimmy %s deployed\n\n[Open frontend](%s)\n\nAPI: `%s`\n\nCommit: `%s`\n' "$DEPLOY_ENV" "$url" "$api_url" "$GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
fi
