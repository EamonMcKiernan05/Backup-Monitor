"""Uptime monitor — ping-based health checks for all homelab targets."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Optional

from ..models import HealthStatus, HostHealth

logger = logging.getLogger(__name__)


class UptimeCollector:
    """Pings configured hosts and reports health status."""

    def __init__(self, targets: list[dict]):
        self.targets = targets

    @staticmethod
    def build_targets(
        proxmox_nodes: list[dict],
        servers: list[dict],
        extra_hosts: list[dict] | None = None,
    ) -> list[dict]:
        """Build the full list of hosts to ping from existing config."""
        targets: list[dict] = []

        for node in proxmox_nodes:
            ip = node.get("ip", node.get("host"))
            targets.append({"name": node.get("name", node.get("host")), "ip": ip})

        for srv in servers:
            ip = srv.get("ip", srv.get("host"))
            targets.append({"name": srv.get("name", srv.get("host")), "ip": ip})

        if extra_hosts:
            for h in extra_hosts:
                targets.append({"name": h.get("name", h.get("ip")), "ip": h.get("ip")})

        return targets

    async def collect(self) -> list[HostHealth]:
        """Ping all targets and return health status."""
        tasks = [self._ping(target) for target in self.targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        health: list[HostHealth] = []
        for result in results:
            if isinstance(result, HostHealth):
                health.append(result)
            elif isinstance(result, Exception):
                logger.error("Uptime check error: %s", result)

        return health

    async def _ping(self, target: dict) -> HostHealth:
        """Ping a single target and return health result."""
        name = target["name"]
        ip = target["ip"]

        try:
            # Run ping with timeout and count
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-c", "2",
                "-W", "3",
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            exit_code = proc.returncode

            if exit_code == 0:
                # Parse latency from output
                latency = self._parse_latency(stdout.decode())
                return HostHealth(
                    name=name,
                    ip=ip,
                    status=HealthStatus.UP,
                    latency_ms=latency,
                    last_check=time.time(),
                )
            else:
                return HostHealth(
                    name=name,
                    ip=ip,
                    status=HealthStatus.DOWN,
                    last_check=time.time(),
                )

        except Exception as e:
            logger.debug("Ping failed for %s (%s): %s", name, ip, e)
            return HostHealth(
                name=name,
                ip=ip,
                status=HealthStatus.UNKNOWN,
                last_check=time.time(),
            )

    @staticmethod
    def _parse_latency(output: str) -> Optional[float]:
        """Extract average latency from ping output."""
        # Look for rtt min/avg/max/mdev line
        for line in output.splitlines():
            if "rtt" in line and "avg" in line:
                # Format: rtt min/avg/max/mdev = 1.234/2.345/3.456/0.123 ms
                try:
                    avg_str = line.split("/")[1].strip().split()[0]
                    return float(avg_str)
                except (IndexError, ValueError):
                    pass
        return None
