#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ROOT_DIR}/.temp/task20-private-tls"
ENV_FILE="${STATE_DIR}/compose.env"
CA_FILE="${STATE_DIR}/caddy-root.crt"
PRIVATE_PORT="8030"

compose() {
    docker compose \
        --project-name "${COMPOSE_PROJECT_NAME}" \
        --project-directory "${ROOT_DIR}" \
        --env-file "${ENV_FILE}" \
        -f "${ROOT_DIR}/compose.yaml" \
        -f "${ROOT_DIR}/compose.private.yaml" \
        "$@"
}

append_github_env() {
    if [[ -z "${GITHUB_ENV:-}" ]]; then
        return
    fi
    {
        printf 'DRAGON_DB_PASSWORD=%s\n' "${DRAGON_DB_PASSWORD}"
        printf 'DRAGON_PRIVATE_PORT=%s\n' "${PRIVATE_PORT}"
        printf 'DRAGON_PRIVATE_PUBLISHED_PORT=%s\n' "${PRIVATE_PORT}"
        printf 'DRAGON_TASK20_COMPOSE_PROJECT=%s\n' "${COMPOSE_PROJECT_NAME}"
        printf 'DRAGON_TEST_PRIVATE_CA=%s\n' "${CA_FILE}"
    } >>"${GITHUB_ENV}"
}

setup() {
    python - "${PRIVATE_PORT}" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.bind(("127.0.0.1", port))
PY

    umask 077
    mkdir -p "${STATE_DIR}"
    DRAGON_DB_PASSWORD="$(openssl rand -hex 32)"
    export DRAGON_DB_PASSWORD
    COMPOSE_PROJECT_NAME="dragon-radar-task20-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-$$}"
    export COMPOSE_PROJECT_NAME
    export DRAGON_PRIVATE_PORT="${PRIVATE_PORT}"
    export DRAGON_PRIVATE_PUBLISHED_PORT="${PRIVATE_PORT}"

    if [[ -n "${GITHUB_ENV:-}" ]]; then
        printf '::add-mask::%s\n' "${DRAGON_DB_PASSWORD}"
    fi
    {
        printf 'DRAGON_DB_PASSWORD=%s\n' "${DRAGON_DB_PASSWORD}"
        printf 'DRAGON_PRIVATE_PORT=%s\n' "${PRIVATE_PORT}"
        printf 'DRAGON_PRIVATE_PUBLISHED_PORT=%s\n' "${PRIVATE_PORT}"
        printf 'COMPOSE_PROJECT_NAME=%s\n' "${COMPOSE_PROJECT_NAME}"
    } >"${ENV_FILE}"
    append_github_env

    compose build api web
    compose up --detach --wait db
    compose run --rm api migrate
    compose run --rm api owner-init >/dev/null
    compose up --detach --wait web api

    compose exec -T web \
        cat /data/caddy/pki/authorities/local/root.crt >"${CA_FILE}.tmp"
    mv "${CA_FILE}.tmp" "${CA_FILE}"
    openssl x509 -in "${CA_FILE}" -noout -checkend 0
    openssl verify -CAfile "${CA_FILE}" "${CA_FILE}"
    curl --fail --silent --show-error \
        --cacert "${CA_FILE}" \
        --resolve "localhost:${PRIVATE_PORT}:127.0.0.1" \
        "https://localhost:${PRIVATE_PORT}/health/ready" >/dev/null
}

teardown() {
    local status=0
    if [[ -f "${ENV_FILE}" ]]; then
        COMPOSE_PROJECT_NAME="$(
            sed -n 's/^COMPOSE_PROJECT_NAME=//p' "${ENV_FILE}"
        )"
        export COMPOSE_PROJECT_NAME
        compose down --volumes --remove-orphans || status=$?
    elif [[ -n "${DRAGON_TASK20_COMPOSE_PROJECT:-}" ]]; then
        COMPOSE_PROJECT_NAME="${DRAGON_TASK20_COMPOSE_PROJECT}"
        export COMPOSE_PROJECT_NAME
        export DRAGON_DB_PASSWORD="${DRAGON_DB_PASSWORD:-teardown-only}"
        mkdir -p "${STATE_DIR}"
        {
            printf 'DRAGON_DB_PASSWORD=%s\n' "${DRAGON_DB_PASSWORD}"
            printf 'DRAGON_PRIVATE_PORT=%s\n' "${PRIVATE_PORT}"
            printf 'DRAGON_PRIVATE_PUBLISHED_PORT=%s\n' "${PRIVATE_PORT}"
            printf 'COMPOSE_PROJECT_NAME=%s\n' "${COMPOSE_PROJECT_NAME}"
        } >"${ENV_FILE}"
        compose down --volumes --remove-orphans || status=$?
    fi
    rm -rf "${STATE_DIR}"
    return "${status}"
}

case "${1:-}" in
    setup)
        setup
        ;;
    teardown)
        teardown
        ;;
    *)
        printf 'usage: %s setup|teardown\n' "$0" >&2
        exit 2
        ;;
esac
