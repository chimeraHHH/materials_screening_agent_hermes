"""Public, fail-closed errors for the Materials Gateway core service."""

from __future__ import annotations


class MaterialsGatewayError(RuntimeError):
    """Base class for bounded Gateway failures."""

    code = "MATERIALS_GATEWAY_ERROR"


class SubmissionConflictError(MaterialsGatewayError):
    """A submission identifier was reused with a different canonical request."""

    code = "SUBMISSION_CONFLICT"


class RunNotFoundError(MaterialsGatewayError):
    """The requested companion run does not exist."""

    code = "RUN_NOT_FOUND"


class ConcurrentUpdateError(MaterialsGatewayError):
    """A persisted run changed before a state transition could be committed."""

    code = "CONCURRENT_UPDATE"


class StaleInteractionError(MaterialsGatewayError):
    """An action did not target the run's current pending interaction."""

    code = "STALE_INTERACTION"


class IllegalActionError(MaterialsGatewayError):
    """The current interaction does not advertise the requested action."""

    code = "ILLEGAL_ACTION"


class AdapterContractError(MaterialsGatewayError):
    """The inspiration companion returned an invalid state transition."""

    code = "ADAPTER_CONTRACT_ERROR"


class AdapterExecutionError(MaterialsGatewayError):
    """The inspiration companion failed without exposing internal details."""

    code = "ADAPTER_EXECUTION_ERROR"


class ResultUnavailableError(MaterialsGatewayError):
    """A run is not in a terminal state with an auditable result."""

    code = "RESULT_UNAVAILABLE"


class ResultIntegrityError(MaterialsGatewayError):
    """A terminal result failed URI or SHA-256 verification."""

    code = "RESULT_INTEGRITY_ERROR"
