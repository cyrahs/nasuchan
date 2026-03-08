from __future__ import annotations

from nasuchan.clients import (
    BackendApiConflictError,
    BackendApiError,
    BackendApiForbiddenError,
    BackendApiInternalServerError,
    BackendApiNotFoundError,
    BackendApiTransportError,
    BackendApiUnauthorizedError,
    BackendApiUnprocessableError,
)


def build_backend_user_message(exc: BackendApiError) -> str:
    if isinstance(exc, (BackendApiUnauthorizedError, BackendApiForbiddenError)):
        return 'Backend authentication failed. Check the configured API token.'
    if isinstance(exc, BackendApiNotFoundError):
        return 'Requested resource was not found in the backend.'
    if isinstance(exc, BackendApiConflictError):
        return 'Backend rejected the change because the resource already exists.'
    if isinstance(exc, BackendApiUnprocessableError):
        return 'Backend could not process the request payload.'
    if isinstance(exc, (BackendApiInternalServerError, BackendApiTransportError)):
        return 'Backend is currently unavailable. Check bot logs for details.'
    return 'Backend request failed.'
