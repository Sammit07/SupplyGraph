"""Vercel entrypoint (service `backend`, entrypoint ``main:app``)."""

from supplygraph.api import create_app

app = create_app()
