"""Index local Python repositories into Neo4j and query the resulting graph.

Static analysis uses :mod:`ast` only. Indexed files are never imported or executed.
Call resolution is best-effort and intentionally incomplete for dynamic Python.
"""

__version__ = "0.1.0"
