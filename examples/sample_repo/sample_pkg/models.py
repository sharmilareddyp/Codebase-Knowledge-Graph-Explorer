"""Domain models for the sample repository."""


class User:
    """A user account."""

    def __init__(self, name: str) -> None:
        self.name = name

    def rename(self, name: str) -> None:
        """Change the user's name and mark the record touched."""

        self.name = name
        self._touch()

    def _touch(self) -> None:
        """Mark the user as updated."""
