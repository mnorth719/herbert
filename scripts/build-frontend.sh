#!/usr/bin/env bash
# Build the frontend + deposit the bundle into src/herbert/web/static/.
# Idempotent; safe to re-run. Install deps only if missing.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FRONTEND_DIR="$SCRIPT_DIR/../frontend"

cd "$FRONTEND_DIR"

if [ ! -d node_modules ]; then
  echo "[build-frontend] installing deps..."
  npm ci
fi

echo "[build-frontend] building..."
npm run build

echo "[build-frontend] done — static/ is ready for FastAPI to mount."
