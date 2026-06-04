"""FastAPI application with backup monitoring endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .collectors import ProxmoxCollector, TrueNasCollector
from .models import (
    BackupInfo,
    DashboardState,
    ProxmoxNodeStatus,
    ServerStatus,
)

logger = logging.getLogger(__name__)

# Load .env if present
load_dotenv(Path(__file__).parent.parent / ".env")


def load_config() -> dict:
    """Load configuration from config.yaml with env var substitution."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        content = f.read()

    import re

    def _replace(match):
        var_name = match.group(1)
        default = match.group(2) or ""
        return os.environ.get(var_name, default)

    content = re.sub(r"\$\{(\w+)\|([^\}]*)\}", _replace, content)
    return yaml.safe_load(content)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = load_config()

    app = FastAPI(
        title="Backup Monitor",
        description="Homelab backup monitoring dashboard",
        version="0.3.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Initialize collectors
    proxmox_cfg = config.get("proxmox", {})
    truenas_cfg = config.get("truenas", {})

    proxmox_collector = ProxmoxCollector(
        nodes=proxmox_cfg.get("nodes", []),
        user=proxmox_cfg.get("user", "root@pam"),
        password=os.environ.get("PROXMOX_PASSWORD", ""),
        api_port=proxmox_cfg.get("api_port", 8006),
        verify_ssl=proxmox_cfg.get("verify_ssl", False),
        entry_point=proxmox_cfg.get("entry_point", ""),
    )

    # Add passwords to TrueNAS server configs
    for srv in truenas_cfg.get("servers", []):
        key = srv.get("host", "MAIN").replace("-", "_").upper()
        key = key.removeprefix("TRUENAS_")
        password_key = f"TRUENAS_{key}_PASSWORD"
        srv["password"] = os.environ.get(password_key) or srv.get("password", "")
        logger.debug("TrueNAS %s: password_key=%s", srv["name"], password_key)

    truenas_collector = TrueNasCollector(
        servers=truenas_cfg.get("servers", []),
        api_port=truenas_cfg.get("api_port", 443),
        verify_ssl=truenas_cfg.get("verify_ssl", False),
    )

    refresh_interval = config.get("dashboard", {}).get("refresh_interval", 60)

    # Cache for latest state
    cached_state: DashboardState | None = None
    cached_at: float = 0

    async def _collect_state() -> DashboardState:
        nonlocal cached_state, cached_at

        now = time.time()
        if cached_state and (now - cached_at) < refresh_interval:
            return cached_state

        tasks = [
            proxmox_collector.collect(),
            truenas_collector.collect(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_nodes: list[ProxmoxNodeStatus] = []
        all_servers: list[ServerStatus] = []
        all_backups: list[BackupInfo] = []

        for result in results:
            if isinstance(result, Exception):
                logger.error("Collector error: %s", result)
                continue

            if isinstance(result, list) and result and isinstance(result[0], ProxmoxNodeStatus):
                all_nodes.extend(result)
            elif isinstance(result, tuple) and len(result) == 2:
                servers, backups = result
                all_servers.extend(servers)
                all_backups.extend(backups)

        state = DashboardState(
            updated_at=time.time(),
            proxmox_nodes=all_nodes,
            servers=all_servers,
            backups=all_backups,
        )

        cached_state = state
        cached_at = now
        return state

    @app.get("/api/dashboard")
    async def get_dashboard() -> DashboardState:
        """Get the current dashboard state."""
        return await _collect_state()

    @app.get("/api/summary")
    async def get_summary():
        """Get a quick summary of backup status."""
        state = await _collect_state()
        return state.summary

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok", "updated_at": time.time()}

    @app.get("/")
    async def serve_dashboard():
        """Serve the dashboard HTML."""
        return FileResponse(str(static_path / "index.html"))

    return app
