"""Application errors mapped to HTTP responses by the API layer."""


class AppError(Exception):
    """Base error with a stable machine-readable code and HTTP status."""

    error = "error"
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PathNotAllowed(AppError):
    """Raised when a repository path is missing or outside the configured roots."""

    error = "path_not_allowed"
    status_code = 403


class NotFoundError(AppError):
    """Raised when a repository or entity id is not in the graph."""

    error = "not_found"
    status_code = 404


class EmbeddingsUnavailable(AppError):
    """Raised when Q&A is requested without embedding credentials."""

    error = "embeddings_unavailable"
    status_code = 503


class BadRequestError(AppError):
    """Raised when a request is understood but cannot be performed."""

    error = "bad_request"
    status_code = 400


class AnswerError(AppError):
    """Raised when the language-model provider fails during Q&A."""

    error = "answer_failed"
    status_code = 502


class GraphUnavailable(AppError):
    """Raised when Neo4j cannot be reached or rejects a graph operation."""

    error = "graph_unavailable"
    status_code = 503
