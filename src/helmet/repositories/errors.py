"""Explicit persistence errors raised by Helmet repositories."""


class RepositoryError(RuntimeError):
    """Base error for persistence operations."""


class RepositoryValidationError(RepositoryError, ValueError):
    """A write was rejected before reaching the backend."""


class RepositoryReadError(RepositoryError):
    """The backend could not complete a read."""


class RepositoryWriteError(RepositoryError):
    """The backend could not complete a write."""


class RepositoryNotFoundError(RepositoryReadError):
    """The requested row does not exist for the repository owner."""
