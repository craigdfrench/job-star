"""
Job-Star default constants.

These values represent sensible defaults that rarely need to change
between deployments. Environment-specific overrides belong in
config.py (loaded from env vars / .env files).
"""

# ---------------------------------------------------------------------------
# Gatehouse connection defaults
# ---------------------------------------------------------------------------
DEFAULT_GATEHOUSE_ENDPOINT = "http://localhost:8000"
DEFAULT_GATEHOUSE_API_VERSION = "v1"
DEFAULT_GATEHOUSE_HEALTH_PATH = "/healthz"

# ---------------------------------------------------------------------------
# Polling defaults
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_POLL_MAX_INTERVAL_SECONDS = 60.0
DEFAULT_POLL_BACKOFF_FACTOR = 1.5  # exponential-ish backoff per attempt

# ---------------------------------------------------------------------------
# Timeout defaults (seconds)
# ---------------------------------------------------------------------------
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 60.0
DEFAULT_JOB_COMPLETION_TIMEOUT_SECONDS = 3600.0  # 1 hour max per job wait
DEFAULT_SHUTDOWN_GRACE_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Concurrency defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_CONCURRENT_JOBS = 4
DEFAULT_MAX_WORKER_THREADS = 8
DEFAULT_QUEUE_BATCH_SIZE = 10  # jobs fetched per poll cycle

# ---------------------------------------------------------------------------
# Retry defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 1.0
DEFAULT_RETRY_MAX_DELAY_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Auth defaults
# ---------------------------------------------------------------------------
DEFAULT_AUTH_SCHEME = "Bearer"
DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS = 60.0  # refresh token this far before expiry

# ---------------------------------------------------------------------------
# Logging defaults
# ---------------------------------------------------------------------------
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ---------------------------------------------------------------------------
# Runtime modes
# ---------------------------------------------------------------------------
MODE_POLLING = "polling"      # Job-Star polls gatehouse for available jobs
MODE_LISTENING = "listening"  # Job-Star receives push notifications from gatehouse
MODE_HYBRID = "hybrid"         # Both polling and listening

VALID_MODES = (MODE_POLLING, MODE_LISTENING, MODE_HYBRID)
