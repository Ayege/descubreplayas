#!/usr/bin/env bash
# Rebuild the hashed lockfiles from the versions the test suites last ran on.
# Run from the repo root, after updating requirements/tested-versions.txt.
set -euo pipefail
compile() {
  uv pip compile "$1" --output-file "$2" --generate-hashes --python-version 3.11 \
    --python-platform linux --no-header --quiet "${@:3}"
}
tested=(--constraint requirements/tested-versions.txt)
compile requirements/dashboard.in requirements/dashboard.lock "${tested[@]}"
compile requirements/api.in requirements/api.lock "${tested[@]}"
compile requirements.txt requirements/pipeline.lock "${tested[@]}"
compile requirements/ci-tools.in requirements/ci-tools.lock
