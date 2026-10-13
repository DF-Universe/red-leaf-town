#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")" && pwd)"
cd "$repo_root/frontend"

if [ "${1:-}" = "--beta" ]; then
  npx vue-tsc --noEmit
  npx vite build --base=/red-leaf-town-beta/ --outDir dist-beta
  echo "内测站已构建到 frontend/dist-beta，访问 /red-leaf-town-beta/"
  exit 0
fi

npm run build
