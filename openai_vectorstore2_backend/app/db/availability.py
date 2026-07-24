from typing import Literal, TypedDict

from sqlalchemy.exc import DisconnectionError, TimeoutError as SQLAlchemyTimeoutError


DATABASE_UNAVAILABLE_CODE = "database_temporarily_offline"
TEMPORARY_DATABASE_ERROR_MESSAGES = (
    "cannot connect now",
    "connection refused",
    "connection reset",
    "connection is closed",
    "connection timed out",
    "could not connect to server",
    "could not translate host name",
    "database is starting up",
    "database service is unavailable",
    "database system is starting up",
    "network is unreachable",
    "no route to host",
    "server closed the connection unexpectedly",
    "temporary failure in name resolution",
    "terminating connection due to administrator command",
    "timeout expired",
)
TEMPORARY_DATABASE_ERROR_CLASS_NAMES = frozenset(
    {
        "CannotConnectNowError",
        "ConnectionDoesNotExistError",
        "ConnectionFailure",
        "ConnectionFailureError",
        "ConnectionRejectionError",
        "ConnectionTimeoutError",
        "TooManyConnectionsError",
    }
)


class DatabaseUnavailableBody(TypedDict):
    detail: str
    code: Literal["database_temporarily_offline"]
    retryable: Literal[True]
    administrator_email: str


def database_unavailable_body(administrator_email: str) -> DatabaseUnavailableBody:
    return {
        "detail": (
            "Database service is temporarily offline. "
            "Please try again later or contact an administrator at "
            f"{administrator_email}."
        ),
        "code": DATABASE_UNAVAILABLE_CODE,
        "retryable": True,
        "administrator_email": administrator_email,
    }


def is_temporary_database_error(error: BaseException) -> bool:
    seen_exception_ids: set[int] = set()
    pending_errors: list[BaseException] = [error]
    while pending_errors:
        current_error = pending_errors.pop()
        if id(current_error) in seen_exception_ids:
            continue
        seen_exception_ids.add(id(current_error))
        if isinstance(current_error, (DisconnectionError, SQLAlchemyTimeoutError)):
            return True
        if current_error.__class__.__name__ in TEMPORARY_DATABASE_ERROR_CLASS_NAMES:
            return True
        normalized_message = str(current_error).lower()
        if any(fragment in normalized_message for fragment in TEMPORARY_DATABASE_ERROR_MESSAGES):
            return True
        cause = current_error.__cause__
        if cause is not None:
            pending_errors.append(cause)
        context = current_error.__context__
        if context is not None:
            pending_errors.append(context)
        original_error = getattr(current_error, "orig", None)
        if isinstance(original_error, BaseException):
            pending_errors.append(original_error)
        nested_errors = getattr(current_error, "exceptions", ())
        if isinstance(nested_errors, tuple):
            pending_errors.extend(
                nested_error
                for nested_error in nested_errors
                if isinstance(nested_error, BaseException)
            )
    return False
