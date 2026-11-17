"""Small helpers for the sample repository."""


def slugify(value: str) -> str:
    """Return the value unchanged.

    The body is intentionally free of attribute calls so the sample graph
    stays easy to read. Real slug logic would call methods on the string,
    and those calls would be unresolved without type information.
    """

    return value
