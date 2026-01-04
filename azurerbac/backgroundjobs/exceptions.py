"""Exceptions for background jobs module."""


class EmptyFetchResultError(Exception):
    """Raised when a fetch operation returns zero results.

    Azure should always return built-in roles and provider operations.
    Zero results indicates an API issue, auth problem, or misconfiguration.
    """

    def __init__(self, job_name: str) -> None:
        self.job_name = job_name
        super().__init__(f"{job_name}: Fetch returned 0 results - this indicates an error")
