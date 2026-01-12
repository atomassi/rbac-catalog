"""Background jobs exceptions."""


class EmptyFetchResultError(Exception):
    """Fetch returned zero results."""

    def __init__(self, job_name: str) -> None:
        self.job_name = job_name
        super().__init__(f"{job_name}: Fetch returned 0 results")
