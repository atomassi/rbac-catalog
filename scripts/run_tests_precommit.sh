#!/bin/bash
# =============================================================================
# Pre-commit Test Runner
# =============================================================================
# Fast test runner for pre-commit hooks. Runs unit tests by default.
# Exits on first failure for fast feedback.
#
# Usage:
#   ./scripts/run_tests_precommit.sh           # Run unit tests (default)
#   ./scripts/run_tests_precommit.sh unit      # Run unit tests only
#   ./scripts/run_tests_precommit.sh e2e       # Run E2E tests only
#   ./scripts/run_tests_precommit.sh all       # Run both unit + e2e
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source venv/bin/activate
fi

# Config
PORT="${PORT:-8000}"
E2E_DB="${PROJECT_ROOT}/e2e_test.db"
SERVER_PID=""

# Colors (disabled if not a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    BLUE='\033[0;34m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    RED='' GREEN='' BLUE='' YELLOW='' NC=''
fi

log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1" >&2; }

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$E2E_DB"
}

trap cleanup EXIT INT TERM

wait_for_server() {
    local attempt=1
    local max_attempts=30
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "http://127.0.0.1:$PORT/healthz" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    log_error "Server failed to start after ${max_attempts}s"
    return 1
}

run_unit() {
    log_info "Running unit tests..."
    local start_time=$SECONDS
    pytest -x -q --tb=short
    local duration=$((SECONDS - start_time))
    log_success "Unit tests passed (${duration}s)"
}

run_e2e() {
    log_info "Running E2E tests..."
    local start_time=$SECONDS
    
    # Ensure clean state
    cleanup
    
    log_info "Seeding test database..."
    DB_CONNECTION_STRING="sqlite+aiosqlite:///${E2E_DB}" python e2e/seed_data.py
    
    log_info "Starting server on port ${PORT}..."
    DB_CONNECTION_STRING="sqlite+aiosqlite:///${E2E_DB}" \
    ENABLE_ROLE_SCAN=false \
    ENABLE_OPERATIONS_SCAN=false \
        uvicorn rbaccatalog.web.app:app --host 127.0.0.1 --port "$PORT" > /dev/null 2>&1 &
    SERVER_PID=$!
    
    wait_for_server || exit 1
    
    log_info "Running Playwright tests..."
    BASE_URL="http://127.0.0.1:$PORT" npx playwright test
    
    local duration=$((SECONDS - start_time))
    log_success "E2E tests passed (${duration}s)"
}

show_help() {
    cat << EOF
Usage: $0 [command]

Commands:
  unit    Run unit tests only (default)
  e2e     Run E2E tests with seeded database
  all     Run both unit + e2e tests

Options:
  -h, --help    Show this help message

Environment:
  PORT          Server port for E2E tests (default: 8000)
EOF
}

main() {
    case "${1:-unit}" in
        unit)
            run_unit
            ;;
        e2e)
            run_e2e
            ;;
        all)
            run_unit
            echo ""
            run_e2e
            ;;
        -h|--help)
            show_help
            ;;
        *)
            log_error "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
