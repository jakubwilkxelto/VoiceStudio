#!/bin/sh
# Run the fork's tests inside the Docker image, on the SQLite the server ships.
#
# The image's Python can carry an older SQLite than a dev machine (3.50 in the
# image vs 3.53 on Windows), and some SQL the scoping relies on behaves
# differently between them. Run this after changing backend/xelto/ or after
# an upstream merge that touches the database code.
#
#   sh scripts/xelto/test_in_image.sh [image] [extra pytest args, e.g. -k e2e]
#
# The image defaults to the local trial's (docker-compose.local.yml). pytest
# is installed into the throwaway container from PyPI.
set -eu
image="${1:-xelto-smoke-voicestudio}"
[ "$#" -gt 0 ] && shift
repo="$(cd "$(dirname "$0")/../.." && pwd)"
MSYS_NO_PATHCONV=1 docker run --rm -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$repo:/src" -w /src --entrypoint sh "$image" -c '
    python -c "import sqlite3; print(\"SQLite\", sqlite3.sqlite_version)"
    pip install -q --root-user-action=ignore pytest
    python -m pytest tests/xelto_fork -q -p no:cacheprovider "$@"
  ' sh "$@"
