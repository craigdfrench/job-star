#!/usr/bin/env bash
#
# run_telegram_bot.sh — Start the Job-Star Telegram intake bot.
#
# Usage:
#   ./scripts/run_telegram_bot.sh              # run in foreground
#   TELEGRAM_BOT_LOG_FILE=logs/bot.log \
#     ./scripts/run_telegram_bot.sh            # run with file logging
#
# Environment variables (see .env.example for full list):
#   TELEGRAM_BOT_TOKEN       — Bot API token from @BotFather (required)
#   TELEGRAM_BOT_LOG_LEVEL   — DEBUG | INFO | WARNING | ERROR (default: INFO)
#   TELEGRAM_BOT_LOG_FILE    — Path to log file (optional)
#   TELEGRAM_ALLOWED_USER_IDS — Comma-separated Telegram user IDs (optional)
#
set -euo pipefail

# Resolve project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# Load .env if present and python-dotenv is available
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env" 2>/dev/null || true
  set +a
fi

# Validate required env
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN is not set." >&2
  echo "Set it in .env or export it before running this script." >&2
  exit 1
fi

# Ensure we're using the right Python
PYTHON="${PYTHON:-python3}"

echo "Starting Job-Star Telegram bot..."
echo "  Project root: ${PROJECT_ROOT}"
echo "  Python:       ${PYTHON}"
echo "  Log level:    ${TELEGRAM_BOT_LOG_LEVEL:-INFO}"
echo "---"

# Run the polling loop
exec "${PYTHON}" -m services.telegram_bot.run_polling
