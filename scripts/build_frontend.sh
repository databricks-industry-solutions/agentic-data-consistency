#!/bin/bash

# Builds the React frontend and copies it into the app source, where FastAPI
# serves it from backend/static/. Run this before `databricks bundle deploy`.
# backend/static/ is gitignored; databricks.yml's sync.include still uploads it.

set -e  # Exit on any error

APP_DIR="$(cd "$(dirname "$0")/../apps/dq-proposal-agent" && pwd)"

cd "$APP_DIR/frontend"
yarn install --frozen-lockfile
yarn build

rm -rf "$APP_DIR/backend/static"
cp -R "$APP_DIR/frontend/dist" "$APP_DIR/backend/static"
echo "Frontend built into $APP_DIR/backend/static"
