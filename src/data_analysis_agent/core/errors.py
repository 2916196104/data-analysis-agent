"""Shared validation errors for data inputs and database contracts."""


class ReviewDataError(ValueError):
    """A review input or database does not satisfy the application contract."""
