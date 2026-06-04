"""Proxmox backup collector — queries vzdump backup data."""

from __future__ import annotations

import time
import logging
from typing import Optional

import httpx

from ..models import BackupInfo, BackupStatus, ServerStatus

logger = logging.getLogger(__name__)


def _human_size(bytes_val: Optional[float]) -> str:
    if bytes_val is None or bytes_val == 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(bytes_val) < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def _human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _time_ago(ts: Optional[float]) -> str:
    if ts is None:
        return "never"
    diff = time.time() - ts
    if diff < 60:
        return f"{diff:.0f}s ago"
    if diff < 3600:
        return f"{diff / 60:.0f}m ago"
    if diff < 86400:
        return f"{diff / 3600:.1f}h ago"
    return f"{diff / 86400:.1f}d ago"


def _time_until(ts: Optional[float]) -> str:
    if ts is None:
        return "not scheduled"
    diff = ts - time.time()
    if diff < 0:
        return "overdue"
    if diff < 60:
        return f"in {diff:.0f}s"
    if diff < 3600:
        return f"in {diff / 60:.0f}m"
    if diff < 86400:
        return f"in {diff / 3600:.1f}h"
    return f"in {diff / 86400:.1f}d"


class ProxmoxCollector:
    """Collects backup data from Proxmox nodes via the REST API."""

    def __init__(
        self,
        nodes: list[dict],
        user: str = "root@pam",
        password: str = "",
        api_port: int = 8006,
        verify_ssl: bool = False,
    ):
        self.nodes = nodes
        self.user = user
        self.password = password
        self.api_port = api_port
        self.verify_ssl = verify_ssl

    async def collect(
        self,
    ) -> tuple[list[ServerStatus], list[BackupInfo]]:
        """Collect backup data from all configured Proxmox nodes."""
        servers: list[ServerStatus] = []
        backups: list[BackupInfo] = []

        for node_cfg in self.nodes:
            host = node_cfg["host"]
            name = node_cfg.get("name", host)
            server_status = ServerStatus(name=name, host=host)

            try:
                client = self._get_client()

                # Login
                resp = await client.post(
                    f"https://{host}:{self.api_port}/api2/json/access/ticket",
                    data={"username": self.user, "password": self.password},
                )
                if resp.status_code == 401:
                    server_status.connection_error = "Authentication failed"
                    servers.append(server_status)
                    await client.aclose()
                    continue

                resp.raise_for_status()
                data = resp.json().get("data", {})
                ticket = data.get("ticket", "")
                csrf = data.get("CSRFPreventionToken", "")
                server_status.connected = True

                # Get node names from the cluster
                nodes_resp = await client.get(
                    f"https://{host}:{self.api_port}/api2/json/nodes",
                    headers={
                        "Cookie": f"PVEAuthCookie={ticket}",
                        "CSRFPreventionToken": csrf,
                    },
                )
                node_list = nodes_resp.json().get("data", [])

                # Collect vzdump tasks from each node
                seen_upids = set()
                for node_info in node_list:
                    node_name = node_info.get("node", "")
                    node_backups = await self._get_vzdump_tasks(
                        client, host, name, node_name, ticket, csrf
                    )
                    for b in node_backups:
                        upid = b.extra.get("upid", "")
                        if upid and upid not in seen_upids:
                            seen_upids.add(upid)
                            backups.append(b)

            except Exception as e:
                logger.error("Error collecting from Proxmox %s: %s", host, e)
                server_status.connection_error = str(e)

            servers.append(server_status)

        return servers, backups

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=httpx.Timeout(30),
        )

    async def _api_get(
        self,
        client: httpx.AsyncClient,
        host: str,
        ticket: str,
        csrf: str,
        path: str,
    ) -> Optional[list]:
        """Make an authenticated GET request."""
        url = f"https://{host}:{self.api_port}/api2/json/{path}"
        headers = {
            "Cookie": f"PVEAuthCookie={ticket}",
            "CSRFPreventionToken": csrf,
        }
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return data if isinstance(data, list) else [data]
        except Exception as e:
            logger.debug("Proxmox API call failed for %s: %s", path, e)
            return None

    async def _get_vzdump_tasks(
        self,
        client: httpx.AsyncClient,
        host: str,
        node_label: str,
        node_name: str,
        ticket: str,
        csrf: str,
    ) -> list[BackupInfo]:
        """Get vzdump backup tasks for a node."""
        backups = []

        tasks = await self._api_get(
            client, host, ticket, csrf, f"nodes/{node_name}/tasks"
        )
        if not tasks:
            return backups

        # Filter for vzdump tasks
        vzdump_tasks = [t for t in tasks if t.get("type") == "vzdump"]
        if not vzdump_tasks:
            return backups

        # Sort by start time (most recent first)
        vzdump_tasks.sort(
            key=lambda t: t.get("starttime", 0) or 0, reverse=True
        )

        # Take the most recent task
        latest = vzdump_tasks[0]
        upid = latest.get("upid", "backup")
        # Extract VMID from UPID or use a short identifier
        vmid = upid.split(":")[-3] if ":" in upid else upid[:12]

        status = BackupStatus.UNKNOWN
        status_msg = latest.get("status", "unknown")
        errors = []
        warnings = []

        if status_msg == "OK":
            status = BackupStatus.SUCCESS
            status_msg = "Completed successfully"
        elif status_msg in ("FAILED", "interrupted by signal", "ERROR"):
            status = BackupStatus.FAILED
            status_msg = "Failed"
            err = latest.get("errstring", "")
            if err:
                errors.append(err)
        elif status_msg == "RUNNING":
            status = BackupStatus.RUNNING
            status_msg = "Running"

        starttime = latest.get("starttime")
        endtime = latest.get("endtime")
        duration = (endtime - starttime) if (endtime and starttime) else None

        backups.append(BackupInfo(
            name=f"VZDump — {node_name}",
            source=node_label,
            status=status,
            status_message=status_msg,
            backup_type="vzdump",
            last_run=starttime,
            last_duration=duration,
            time_since_last=_time_ago(starttime),
            errors=errors,
            warnings=warnings,
            extra={
                "node": node_name,
                "upid": upid,
                "status_raw": latest.get("status"),
                "errstring": latest.get("errstring", ""),
            },
        ))

        return backups
