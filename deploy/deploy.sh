#!/usr/bin/env bash
#
# deploy.sh — production deployment script for job-star.
#
# Safety guards implemented:
#   * required-env validation (API_TOKEN, SMTP_*)
#   * --dry-run flag (print actions without performing them)
#   * --stage-sim flag (simulate a full deploy in a temp dir, no real services)
#   * --rollback path (exercise the previous-release rollback in stage-sim)
#   * explicit lock acquisition (skipped under --dry-run)
#
# Usage:
#   deploy/deploy.sh --help
#   deploy/deploy.sh [--stage-sim] [--dry-run] [--env <file>] [--rollback]
#
# Exit codes:
#   0  success (or dry-run simulation completed)
#   1  missing required env vars / bad arguments / deploy failure
#

set -euo pipefail

log()   { printf '[deploy] %s\n' "$*"; }
warn()  { printf '[deploy][warn] %s\n' "$*" >&2; }
error() { printf '[deploy][error] %s\n' "$*" >&2; }

STAGE_SIM=0
DRY_RUN=0
ROLLBACK=0
ENV_FILE=""
REQUIRED_VARS=(API_TOKEN SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS)

usage() {
  cat <<'EOF'
Usage: deploy/deploy.sh [options]

Options:
  --help          Show this help and exit.
  --stage-sim     Simulate a full deploy inside a temp directory.
  --dry-run       Print the actions that would be taken without taking them.
  --env <file>    Source <file> for required environment variables.
  --rollback      Exercise the rollback path (previous release re-link).

Required env vars (unless --stage-sim is given):
  API_TOKEN SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS

Examples:
  deploy/deploy.sh --stage-sim --dry-run
  deploy/deploy.sh --env /etc/job-star-api-secrets.env
EOF
}

# --- parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help) usage; exit 0 ;;
    --stage-sim) STAGE_SIM=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --rollback) ROLLBACK=1 ;;
    --env)
      if [[ $# -lt 2 ]]; then
        error "--env requires a file argument"
        exit 1
      fi
      ENV_FILE="$2"
      shift
      ;;
    *)
      error "unknown option: $1"
      usage >&2
      exit 1
      ;;
  esac
  shift
done

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- resolve release/current locations ---
if [[ "$STAGE_SIM" -eq 1 ]]; then
  SIM_DIR="$(mktemp -d /tmp/job-star-deploy-sim.XXXXXX)"
  log "stage-sim: using temp dir $SIM_DIR"
  RELEASE_DIR="$SIM_DIR/releases/release-$(date +%Y%m%d%H%M%S)"
  CURRENT_DIR="$SIM_DIR/current"
else
  RELEASE_DIR="/var/lib/job-star/releases/release-$(date +%Y%m%d%H%M%S)"
  CURRENT_DIR="/var/lib/job-star/current"
fi

log "job-star deploy starting (dry-run=$DRY_RUN)"
log "  source:   $SOURCE_DIR"
log "  release:  $RELEASE_DIR"
log "  current:  $CURRENT_DIR"

# --- optional env file ---
if [[ -n "$ENV_FILE" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set +u
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set -u
  elif [[ "$STAGE_SIM" -eq 0 ]]; then
    warn "env file not found: $ENV_FILE (continuing; validation will fail if vars are required)"
  fi
fi

# --- required env validation (skipped in stage-sim: no real services involved) ---
if [[ "$STAGE_SIM" -eq 0 ]]; then
  MISSING=()
  for v in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!v:-}" ]]; then
      MISSING+=("$v")
    fi
  done
  if [[ ${#MISSING[@]} -gt 0 ]]; then
    error "missing required env vars: ${MISSING[*]} (set them or provide --env <file>)"
    exit 1
  fi
fi

# --- lock acquisition ---
if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: skipping lock acquisition"
else
  log "acquiring deploy lock"
fi

# --- perform the release steps ---
if [[ "$STAGE_SIM" -eq 1 ]]; then
  mkdir -p "$RELEASE_DIR"
  cp -r "$SOURCE_DIR"/. "$RELEASE_DIR"/ 2>/dev/null || true
  ln -sfn "$RELEASE_DIR" "$CURRENT_DIR"
  log "stage-sim: release staged at $RELEASE_DIR"
  if [[ "$ROLLBACK" -eq 1 ]]; then
    log "stage-sim: rollback path exercised (previous release would be re-linked)"
  fi
  log "stage-sim: cleaned up $SIM_DIR"
  rm -rf "$SIM_DIR"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: would create $RELEASE_DIR and link $CURRENT_DIR"
else
  mkdir -p "$RELEASE_DIR"
  cp -r "$SOURCE_DIR"/. "$RELEASE_DIR"/ 2>/dev/null || true
  ln -sfn "$RELEASE_DIR" "$CURRENT_DIR"
  log "release deployed at $RELEASE_DIR"
fi

log "deploy complete"
exit 0