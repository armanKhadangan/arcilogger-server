#!/usr/bin/env bash
# Setup script for a Claude Code cloud environment working on ARCILOGGER (Ubuntu 24.04 image).
#
# The agent runs it at the start of every session (CLAUDE.md); it can also be pasted as a cloud
# environment's setup script. It installs PostgreSQL 16 with the test role the suites use (as the
# CI job does: arci_test / arci_test, CREATEDB, no superuser) and starts the server. Running it
# again is harmless.
# The repository's own dependencies are installed by the session (uv sync --locked
# --all-packages; cd web && npm ci), see CLAUDE.md. TimescaleDB, PowerShell and Caddy are not
# installed: their tests skip here and run in CI.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! dpkg -s postgresql-16 >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq postgresql-16 postgresql-client-16
fi
service postgresql start

su postgres -c "psql -v ON_ERROR_STOP=1 -q" <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'arci_test') THEN
        CREATE ROLE arci_test LOGIN PASSWORD 'arci_test' CREATEDB;
    END IF;
END
$$;
SQL

# Every gate runs through uv; the cloud image normally has it already.
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "ARCILOGGER cloud setup done: $(psql --version), test role arci_test on 127.0.0.1:5432"
