"""Entry point for the voicebox backend.

Imports the configured FastAPI app and provides a ``python -m backend.main``
entry point for development.
"""

import argparse
import uvicorn

from .app import app  # noqa: F401 -- re-export for uvicorn "backend.main:app"
from . import config, database, roles

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="voicebox backend server")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (use 0.0.0.0 for remote access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Data directory for database, profiles, and generated audio",
    )
    parser.add_argument(
        "--role",
        choices=roles.ROLES,
        default="all",
        help="all: 1 process (default). main: owns state. tts/stt: inference workers",
    )
    args = parser.parse_args()

    roles.set_role(args.role)
    if args.data_dir:
        config.set_data_dir(args.data_dir)

    database.init_db()

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
