"""ASGI entrypoint for Vercel.

The application factory lives in ``codebase_kg.api.app``. Vercel looks for a
module-level variable named ``app`` in ``app.py`` at the repository root.
"""

from codebase_kg.api.app import create_app

app = create_app()
