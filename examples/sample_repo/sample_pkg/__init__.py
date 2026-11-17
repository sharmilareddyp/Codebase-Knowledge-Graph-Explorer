"""Sample package for the codebase knowledge graph."""

from sample_pkg.utils import slugify


def package_slug(value: str) -> str:
    """Slugify a value from the package root."""

    return slugify(value)
