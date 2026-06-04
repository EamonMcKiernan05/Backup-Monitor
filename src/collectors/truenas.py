"""TrueNAS backup collector — queries replication and snapshot tasks."""

from __future__ import annotations

import re
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


def _size_str_to_bytes(size_str: Optional[str]) -> Optional[float]:
    """Parse size string like '41.9 GiB' to bytes."""
    if not size_str:
        return None
    match = re.search(r"([\d.]+)\s*(KiB|MiB|GiB|TiB|B)", size_str)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return value * multipliers.get(unit, 1)


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


def _mongodb_date(date_obj: Optional[dict]) -> Optional[float]:
    """Parse MongoDB-style date object {'$date': milliseconds} to unix timestamp."""
    if not date_obj:
        return None
    if isinstance(date_obj, dict) and "$date" in date_obj:
        return date_obj["$date"] / 1000  # ms → seconds
    return None


class TrueNasCollector:
    """Collects backup data from TrueNAS servers via the REST API.

    TrueNAS Scale uses HTTP Basic Authentication.
    """

    def __init__(
        self,
        servers: list[dict],
        api_port: int = 443,
        verify_ssl: bool = False,
    ):
        self.servers = servers
        self.api_port = api_port
        self.verify_ssl = verify_ssl

    async def collect(
        self,
    ) -> tuple[list[ServerStatus], list[BackupInfo]]:
        """Collect backup data from all configured TrueNAS servers."""
        servers: list[ServerStatus] = []
        backups: list[BackupInfo] = []

        for srv_cfg in self.servers:
            host = srv_cfg["host"]
            name = srv_cfg.get("name", host)
            user = srv_cfg.get("user", "admin")
            password = srv_cfg.get("password", "")
            server_status = ServerStatus(name=name, host=host)

            try:
                async with httpx.AsyncClient(
                    verify=self.verify_ssl,
                    timeout=httpx.Timeout(30),
                    auth=httpx.BasicAuth(user, password),
                    base_url=f"https://{host}:{self.api_port}/api/v2.0",
                ) as client:
                    # Test connection
                    try:
                        resp = await client.get(
                            "/system/general/timezone_choices"
                        )
                        resp.raise_for_status()
                        server_status.connected = True
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 401:
                            server_status.connection_error = (
                                "Authentication failed — check credentials"
                            )
                        else:
                            server_status.connection_error = f"HTTP {e.response.status_code}"
                        servers.append(server_status)
                        continue
                    except Exception as e:
                        server_status.connection_error = str(e)
                        servers.append(server_status)
                        continue

                    # Get replication tasks
                    repl_backups = await self._get_replication(client, host, name)
                    backups.extend(repl_backups)

                    # Get periodic snapshot tasks
                    snap_backups = await self._get_snapshots(client, host, name)
                    backups.extend(snap_backups)

            except Exception as e:
                logger.error("Error collecting from TrueNAS %s: %s", host, e)
                server_status.connection_error = str(e)

            servers.append(server_status)

        return servers, backups

    async def _api_get(
        self, client: httpx.AsyncClient, path: str
    ) -> Optional[list]:
        """Make an authenticated GET request."""
        try:
            resp = await client.get(path)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else [data]
        except Exception as e:
            logger.debug("TrueNAS API call failed for %s: %s", path, e)
            return None

    async def _get_replication(
        self, client: httpx.AsyncClient, host: str, server_name: str
    ) -> list[BackupInfo]:
        """Get replication tasks with their latest job results."""
        backups = []

        tasks = await self._api_get(client, "/replication")
        if not tasks:
            return backups

        for task in tasks:
            if not task.get("enabled", True):
                continue

            task_name = task.get("name", "unnamed replication")
            direction = task.get("direction", "PUSH")
            source_datasets = task.get("source_datasets", [])
            target_dataset = task.get("target_dataset", "")

            # Parse state info
            state_info = task.get("state", {})
            job_info = task.get("job", {})

            if not job_info:
                # No job info yet — show as pending
                backups.append(BackupInfo(
                    name=f"Replication: {task_name}",
                    source=server_name,
                    status=BackupStatus.PENDING,
                    status_message="No recent runs",
                    backup_type="replication",
                    extra={
                        "direction": direction,
                        "source_datasets": source_datasets,
                        "target_dataset": target_dataset,
                    },
                ))
                continue

            # Extract job details
            job_state = job_info.get("state", "UNKNOWN")
            time_started = _mongodb_date(job_info.get("time_started"))
            time_finished = _mongodb_date(job_info.get("time_finished"))
            job_error = job_info.get("error")
            job_exception = job_info.get("exception")
            progress = job_info.get("progress", {})

            # Parse progress description for size info
            progress_desc = progress.get("description", "")
            progress_pct = progress.get("percent", 0)

            # Extract sizes from progress description
            # e.g. "Sending 1 of 1: dataset@snap [total 41.9 GiB of 41.9 GiB]"
            total_size = None
            data_transferred = None
            size_match = re.search(
                r"\[total\s+([\d.]+)\s*(KiB|MiB|GiB|TiB)\s+of\s+([\d.]+)\s*(KiB|MiB|GiB|TiB)\]",
                progress_desc,
            )
            if size_match:
                data_transferred = _size_str_to_bytes(
                    f"{size_match.group(1)} {size_match.group(2)}"
                )
                total_size = _size_str_to_bytes(
                    f"{size_match.group(3)} {size_match.group(4)}"
                )

            # Determine status
            status = BackupStatus.UNKNOWN
            status_msg = ""
            errors = []
            warnings = []

            if job_state == "SUCCESS":
                status = BackupStatus.SUCCESS
                status_msg = "Last replication successful"
            elif job_state == "ERROR" or job_state == "FAILED":
                status = BackupStatus.FAILED
                status_msg = "Last replication failed"
                errors.append(job_error or job_exception or "Replication job failed")
            elif job_state == "RUNNING":
                status = BackupStatus.RUNNING
                status_msg = f"Running ({progress_pct}%)"
            else:
                status_msg = f"State: {job_state}"

            # Check state warnings
            state_warnings = state_info.get("warnings", [])
            warnings.extend(state_warnings)

            # Duration
            last_duration = None
            if time_finished and time_started:
                last_duration = time_finished - time_started

            # Last snapshot info
            last_snapshot = state_info.get("last_snapshot", "")

            backups.append(BackupInfo(
                name=f"Replication: {task_name}",
                source=server_name,
                status=status,
                status_message=status_msg,
                backup_type="replication",
                last_run=time_started,
                last_duration=last_duration,
                data_transferred=data_transferred,
                data_transferred_human=_human_size(data_transferred),
                total_size=total_size,
                total_size_human=_human_size(total_size),
                time_since_last=_time_ago(time_started),
                errors=errors,
                warnings=warnings,
                extra={
                    "direction": direction,
                    "source_datasets": source_datasets,
                    "target_dataset": target_dataset,
                    "last_snapshot": last_snapshot,
                    "progress_percent": progress_pct,
                    "job_id": job_info.get("id"),
                },
            ))

        return backups

    async def _get_snapshots(
        self, client: httpx.AsyncClient, host: str, server_name: str
    ) -> list[BackupInfo]:
        """Get periodic snapshot tasks."""
        backups = []

        tasks = await self._api_get(client, "/pool/snapshottask")
        if not tasks:
            return backups

        for task in tasks:
            if not task.get("enabled", True):
                continue

            task_name = task.get("name") or task.get("dataset") or f"task-{task.get('id', '?')}"
            datasets = task.get("datasets", [])
            lifetime = task.get("lifetime", 0)

            backups.append(BackupInfo(
                name=f"Snapshots: {task_name}",
                source=server_name,
                status=BackupStatus.PENDING,
                status_message="Snapshot schedule active",
                backup_type="snapshot",
                extra={
                    "lifetime": lifetime,
                    "recursive": task.get("recursive", False),
                    "datasets": datasets[:5],
                    "id": task.get("id"),
                },
            ))

        return backups
