"""Application service for the sample repository."""

from sample_pkg.models import User
from sample_pkg.utils import slugify


def build_user(name: str) -> User:
    """Create a user, slugify the name, and demonstrate an unresolved call."""

    user = User(name)
    slug = slugify(name)
    user.rename(slug)
    dynamic = getattr(user, "rename")
    dynamic(name)
    return user


class Directory:
    """Stores users."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def add(self, user: User) -> None:
        """Store a user.

        ``user._touch()`` is a call on a local instance. Static analysis records
        it as unresolved instead of guessing the target.
        """

        self._users[user.name] = user
        user._touch()
