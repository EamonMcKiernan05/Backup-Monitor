#!/usr/bin/env python3
"""Backup Monitor — entry point."""

from __future__ import annotations

import logging
import os

from src.api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Start the backup monitor dashboard."""
    import uvicorn

    app = create_app()
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8501"))

    logger.info("Starting Backup Monitor on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
