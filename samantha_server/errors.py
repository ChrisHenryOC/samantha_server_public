"""Project-level typed exception hierarchy for samantha_server."""

from pathlib import Path

__all__ = [
    "LLMClientError",
    "LLMInferenceError",
    "LLMModelLoadError",
    "LLMTimeoutError",
    "MisconfiguredEnvironmentError",
    "PHIBoundaryError",
    "ReceiptPersistenceError",
    "SamanthaError",
    "ScenarioCorpusError",
    "ScenarioNotFoundError",
    "ShutdownError",
]


class SamanthaError(Exception):
    """Base class for all samantha_server exceptions."""


class ScenarioNotFoundError(SamanthaError):
    """Raised when a scenario_id is not found in the index."""

    def __init__(self, *, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        super().__init__(f"Scenario not found: {scenario_id}")


class ScenarioCorpusError(SamanthaError):
    """Raised when load_scenarios finds no usable scenarios.

    directory: the directory it tried to load.
    malformed_files: files it rejected. Empty when the cause is a missing
        directory; non-empty when the cause is all files being malformed.
    """

    def __init__(
        self,
        *,
        directory: Path,
        malformed_files: tuple[Path, ...] = (),
    ) -> None:
        self.directory = directory
        self.malformed_files = malformed_files
        if malformed_files:
            files_str = ", ".join(str(p) for p in malformed_files)
            msg = f"No usable scenarios in {directory}; malformed files: {files_str}"
        else:
            msg = f"No usable scenarios in {directory}"
        super().__init__(msg)


class MisconfiguredEnvironmentError(SamanthaError):
    """Raised at import time by config.py when a required env var is missing or malformed.

    Takes a single positional message string. Error messages must NOT include
    the raw env value (G18 no-leak invariant).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class LLMClientError(SamanthaError):
    """Base class for all LLM-side runtime errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class LLMTimeoutError(LLMClientError):
    """Raised when an LLM call exceeds its latency budget.

    model_id: the model that timed out.
    timeout_us: the budget it exceeded, in microseconds.

    Currently unused (defined here so the LLMClient Protocol's error
    contract is complete from the foundations PR).
    """

    def __init__(self, *, model_id: str, timeout_us: int) -> None:
        self.model_id = model_id
        self.timeout_us = timeout_us
        super().__init__(f"LLM call to model '{model_id}' timed out after {timeout_us} µs")


class LLMModelLoadError(LLMClientError):
    """Raised when an LLM client fails to load its model artifact.

    model_path: path to the model that could not be loaded.
    cause: human-readable description of the load failure.

    **Privacy note.** The ``cause`` field is
    populated from the underlying exception's ``__str__`` and may
    include host filesystem paths from a wrapped FileNotFoundError /
    PermissionError. Acceptable for local developer-tool error
    messages; **callers MUST NOT surface ``cause`` verbatim to remote
    audit logs** (it could leak operator home-directory paths).
    Receipt-side audit fields should use ``model_path`` (which is
    config-driven) and a controlled-vocabulary failure-class enum
    rather than the free-form ``cause`` string.
    """

    def __init__(self, *, model_path: str, cause: str) -> None:
        self.model_path = model_path
        self.cause = cause
        super().__init__(f"Failed to load LLM model at '{model_path}': {cause}")


class LLMInferenceError(LLMClientError):
    """Raised when inference itself fails (e.g., the model crashed mid-generation).

    model_id: the model that failed.
    cause: human-readable description of the inference failure.
    """

    def __init__(self, *, model_id: str, cause: str) -> None:
        self.model_id = model_id
        self.cause = cause
        super().__init__(f"Inference failed on model '{model_id}': {cause}")


class ReceiptPersistenceError(SamanthaError):
    """Raised when a signed receipt cannot be persisted to the SQLite store.

    cause: human-readable description of the failure (disk full, WAL corruption,
        integrity violation, etc.).

    No silent fallback: callers must propagate this error. The engine will not
    return a successful EngineDecision unless the receipt has been persisted.
    """

    def __init__(self, *, cause: str) -> None:
        self.cause = cause
        super().__init__(f"Receipt persistence failed: {cause}")


class ShutdownError(SamanthaError):
    """Raised on a pending payload's future when the server is shutting down.

    Set by aclose() when draining the queue on graceful shutdown. Callers
    awaiting the future will receive this exception, which the events handler
    converts to a 503 Service Unavailable response.
    """

    def __init__(self) -> None:
        super().__init__("Server is shutting down; event was not dispatched.")


class PHIBoundaryError(SamanthaError):
    """Raised when PHI-bearing data violates the HIPAA Safe Harbor envelope.

    age: the offending age value (>89 triggers this guard). Exposed as a
    public attribute for catch-sites that need programmatic access; the
    integer is **deliberately NOT included in __str__** because per HIPAA
    Safe Harbor 45 CFR 164.514(b)(2)(i)(C), "all ages over 89" are a PHI
    element. Forwarding ``str(exc)`` to log aggregators (Sentry,
    Datadog, audit-log capture) would leak the value. Mirrors the G18
    no-leak invariant on env-error messages.
    """

    def __init__(self, *, age: int) -> None:
        self.age = age
        super().__init__(
            "Refusing to construct SafeContext: order age exceeds HIPAA "
            "Safe Harbor cap (>89). Aggregate via min(age, 90) upstream "
            "or strip the age field before LLM-payload construction. "
            "(The offending value is on the .age attribute; it is not "
            "included in this message to avoid leaking PHI through logs.)"
        )
