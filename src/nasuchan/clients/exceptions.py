from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BackendApiError(Exception):
    message: str
    status_code: int | None = None
    error_code: str | None = None
    path: str | None = None
    response_body: str | None = None

    def __str__(self) -> str:
        return self.message


class BackendApiTransportError(BackendApiError):
    pass


class BackendApiBadRequestError(BackendApiError):
    pass


class BackendApiUnauthorizedError(BackendApiError):
    pass


class BackendApiForbiddenError(BackendApiError):
    pass


class BackendApiNotFoundError(BackendApiError):
    pass


class BackendApiConflictError(BackendApiError):
    pass


class BackendApiUnprocessableError(BackendApiError):
    pass


class BackendApiInternalServerError(BackendApiError):
    pass


class BackendApiUnexpectedResponseError(BackendApiError):
    pass
