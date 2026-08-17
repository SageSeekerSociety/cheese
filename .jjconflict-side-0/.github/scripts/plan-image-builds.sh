#!/usr/bin/env bash
# Decide which Docker contexts changed since the last fully successful image
# build. Output is compatible with GITHUB_OUTPUT and intentionally pure apart
# from reading git, so the contract test can cover failed-build catch-up.
set -euo pipefail

output_file="${GITHUB_OUTPUT:-/dev/stdout}"
base_sha="${BASE_SHA:-}"
current_sha="${CURRENT_SHA:-HEAD}"
event_name="${EVENT_NAME:-push}"
ref_type="${REF_TYPE:-branch}"

backend=false
sandbox=false
frontend=false

# Tags and manual runs are explicit release/rebuild requests. A repository with
# no earlier successful build also needs a complete bootstrap.
if [[ "$event_name" != "push" || "$ref_type" == "tag" || -z "$base_sha" ]] \
  || ! git cat-file -e "${base_sha}^{commit}" 2>/dev/null; then
  backend=true
  sandbox=true
  frontend=true
else
  while IFS= read -r -d '' changed_path; do
    case "$changed_path" in
      backend/*)
        backend=true
        ;;
      cli/*)
        backend=true
        ;;
      frontend/*)
        frontend=true
        ;;
    esac

    # The production backend bakes backend/sandbox into /app/sandbox, while the
    # same directory is also the context for both runtime images.
    case "$changed_path" in
      backend/sandbox/*)
        sandbox=true
        ;;
    esac
  done < <(git diff --name-only -z "$base_sha" "$current_sha" --)
fi

{
  echo "backend=$backend"
  echo "sandbox=$sandbox"
  echo "frontend=$frontend"
  echo "base_sha=$base_sha"
  echo "base_tag=${base_sha:0:7}"
  echo "current_tag=$(git rev-parse --short=7 "$current_sha")"
} >> "$output_file"
