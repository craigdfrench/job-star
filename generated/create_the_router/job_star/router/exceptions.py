"""Custom exceptions for the Job-Star router."""

class RouterError(Exception):
    """Base exception for all router errors."""


class ModelUnavailableError(RouterError):
    """The selected model is unavailable or rate-limited."""
    def __init__(self, model: str, reason: str = ""):
        self.model = model
        self.reason = reason
        super().__init__(f"Model '{model}' unavailable: {reason}")


class ModelCallError(RouterError):
    """A model call failed after retries."""
    def __init__(self, model: str, original_exception: Exception):
        self.model = model
        self.original = original_exception
        super().__init__(f"Call to '{model}' failed: {original_exception}")


class BudgetExceededError(RouterError):
    """The cost budget for this request has been exceeded."""
    def __init__(self, spent: float, budget: float):
        self.spent = spent
        self.budget = budget
        super().__init__(f"Budget exceeded: spent ${spent:.4f}, budget ${budget:.4f}")


// --- DUPLICATE BLOCK ---

"""Custom exceptions for the Job-Star router."""


class RouterError(Exception):
    """Base exception for all router errors."""


class NoModelAvailableError(RouterError):
    """Raised when no model satisfies the given constraints."""

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class BudgetExceededError(RouterError):
    """Raised when the cost budget is too low for any available model."""


class ModelNotAvailableError(RouterError):
    """Raised when a specifically requested model is not available."""


class RoutingConfigError(RouterError):
    """Raised when routing configuration is invalid."""


// --- DUPLICATE BLOCK ---

"""Custom exceptions for the Job-Star router."""

class RouterError(Exception):
    """Base exception for all router errors."""


class ModelUnavailableError(RouterError):
    """The selected model is unavailable or rate-limited."""
    def __init__(self, model: str, reason: str = ""):
        self.model = model
        self.reason = reason
        super().__init__(f"Model '{model}' unavailable: {reason}")


class ModelCallError(RouterError):
    """A model call failed after retries."""
    def __init__(self, model: str, original_exception: Exception):
        self.model = model
        self.original = original_exception
        super().__init__(f"Call to '{model}' failed: {original_exception}")


class BudgetExceededError(RouterError):
    """The cost budget for this request has been exceeded."""
    def __init__(self, spent: float, budget: float):
        self.spent = spent
        self.budget = budget
        super().__init__(f"Budget exceeded: spent ${spent:.4f}, budget ${budget:.4f}")


// --- DUPLICATE BLOCK ---

"""Custom exceptions for the Job-Star router."""


class RouterError(Exception):
    """Base exception for all router errors."""


class NoModelAvailableError(RouterError):
    """Raised when no model satisfies the given constraints."""

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class BudgetExceededError(RouterError):
    """Raised when the cost budget is too low for any available model."""


class ModelNotAvailableError(RouterError):
    """Raised when a specifically requested model is not available."""


class RoutingConfigError(RouterError):
    """Raised when routing configuration is invalid."""
