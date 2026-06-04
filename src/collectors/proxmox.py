"""Proxmox backup collector — queries per-VM/LXC backup status."""

from __future__ import annotations

import re
import time
import logging
from datetime import datetime
from typing import Optional

import httpx

from ..models import BackupStatus, ProxmoxNodeStatus, VmLxcBackupStatus

logger = logging.getLogger(__name__)


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


def _parse_volid(volid: str) -> Optional[dict]:
    """Parse 'TruenasNFS:backup/vzdump-lxc-100-2026_06_04-16_26_03.tar.zst'."""
    filename = volid.rsplit("/", 1)[-1] if "/" in volid else volid
    m = re.match(
        r"vzdump-(lxc|qemu)-(\d+)-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})",
        filename.replace(".tar.zst", "").replace(".vma.zst", ""),
    )
    if not m:
        return None
    raw = m.group(3)  # e.g. 2026_06_04-16_26_03
    date_part, time_part = raw.split("-", 1)
    date_str = date_part.replace("_", "-")
    time_str = time_part.replace("_", ":")
    dt_str = f"{date_str} {time_str}"
    try:
        ts = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        ts = None
    return {"type": m.group(1), "vmid": int(m.group(2)), "timestamp": ts}


class ProxmoxCollector:
    """Collects per-VM/LXC backup status from Proxmox cluster.

    One collector connects to one cluster and returns per-node status.
    """

    def __init__(
        self,
        nodes: list[dict],
        user: str = "root@pam",
        password: str = "",
        api_port: int = 8006,
        verify_ssl: bool = False,
        entry_point: str = "",
    ):
        self.node_labels = {n["host"]: n.get("name", n["host"]) for n in nodes}
        self.entry_point = entry_point or (nodes[0]["host"] if nodes else "127.0.0.1")
        self.user = user
        self.password = password
        self.api_port = api_port
        self.verify_ssl = verify_ssl

    async def collect(self) -> list[ProxmoxNodeStatus]:
        """Collect per-VM/LXC backup data from the Proxmox cluster."""
        if not self.entry_point:
            return []

        host = self.entry_point

        async with httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=httpx.Timeout(60),
        ) as client:
            return await self._collect_cluster(client, host)

    async def _collect_cluster(
        self, client: httpx.AsyncClient, host: str
    ) -> list[ProxmoxNodeStatus]:
        # Login
        resp = await client.post(
            f"https://{host}:{self.api_port}/api2/json/access/ticket",
            data={"username": self.user, "password": self.password},
        )
        if resp.status_code == 401:
            return [ProxmoxNodeStatus(
                name=self.node_labels.get(host, host), host=host,
                connection_error="Authentication failed",
            )]
        resp.raise_for_status()
        data = resp.json().get("data", {})
        ticket = data.get("ticket", "")
        csrf = data.get("CSRFPreventionToken", "")

        headers = {
            "Cookie": f"PVEAuthCookie={ticket}",
            "CSRFPreventionToken": csrf,
        }

        # Get cluster nodes
        nodes_resp = await client.get(
            f"https://{host}:{self.api_port}/api2/json/nodes", headers=headers
        )
        nodes_resp.raise_for_status()
        node_list = nodes_resp.json().get("data", [])

        # Scan backup storage once
        backup_files = await self._scan_backup_storage(client, host, headers)

        # Build per-node status
        statuses: list[ProxmoxNodeStatus] = []
        for node_info in node_list:
            node_name = node_info.get("node", "")
            label = self.node_labels.get(node_name, node_name)

            status = ProxmoxNodeStatus(name=label, host=host, connected=True)

            # Get QEMU VMs on this node
            qemu_data = await self._api_get(
                client, host, headers, f"nodes/{node_name}/qemu"
            )
            qemu_list = qemu_data if isinstance(qemu_data, list) else []

            # Get LXC containers on this node
            lxc_data = await self._api_get(
                client, host, headers, f"nodes/{node_name}/lxc"
            )
            lxc_list = lxc_data if isinstance(lxc_data, list) else []

            status.vm_count = len(qemu_list)
            status.lxc_count = len(lxc_list)

            for vm in qemu_list:
                vmid = int(vm.get("vmid", 0))
                vm_name = vm.get("name", vm.get("hostname", f"VM {vmid}"))
                status.vm_status[str(vmid)] = self._build_guest_status(
                    vmid, vm_name, "qemu", backup_files
                )

            for lxc in lxc_list:
                vmid = int(lxc.get("vmid", 0))
                vm_name = lxc.get("name", lxc.get("hostname", f"LXC {vmid}"))
                status.lxc_status[str(vmid)] = self._build_guest_status(
                    vmid, vm_name, "lxc", backup_files
                )

            statuses.append(status)

        return statuses

    async def _scan_backup_storage(
        self, client: httpx.AsyncClient, host: str, headers: dict
    ) -> dict:
        """Scan backup storage for vzdump files. Returns {vmid: {timestamp, size}}."""
        storages = await self._api_get(client, host, headers, "storage")
        if not storages:
            return {}

        backup_files: dict[int, dict] = {}

        for storage in storages:
            content = storage.get("content", "")
            if "backup" not in content:
                continue

            store_id = storage.get("storage", "")

            # Get first available node for storage listing
            nodes_resp = await client.get(
                f"https://{host}:{self.api_port}/api2/json/nodes", headers=headers
            )
            try:
                first_node = nodes_resp.json().get("data", [{}])[0].get("node", "")
            except Exception:
                continue
            if not first_node:
                continue

            try:
                content_list = await self._api_get(
                    client, host, headers,
                    f"nodes/{first_node}/storage/{store_id}/content",
                )
                if not isinstance(content_list, list):
                    continue

                for item in content_list:
                    volid = item.get("volid", "")
                    parsed = _parse_volid(volid)
                    if parsed and parsed["timestamp"]:
                        vmid = parsed["vmid"]
                        if vmid not in backup_files or parsed["timestamp"] > backup_files[vmid]["timestamp"]:
                            backup_files[vmid] = {
                                "timestamp": parsed["timestamp"],
                                "size": item.get("size", 0),
                            }
            except Exception as e:
                logger.debug("Error scanning %s: %s", store_id, e)

        return backup_files

    def _build_guest_status(
        self, vmid: int, name: str, vm_type: str, backup_files: dict,
    ) -> VmLxcBackupStatus:
        backup = backup_files.get(vmid)
        if backup and backup.get("timestamp"):
            return VmLxcBackupStatus(
                vmid=vmid, name=name, vm_type=vm_type,
                status=BackupStatus.SUCCESS,
                last_run=backup["timestamp"],
                time_since_last=_time_ago(backup["timestamp"]),
            )
        return VmLxcBackupStatus(
            vmid=vmid, name=name, vm_type=vm_type,
            status=BackupStatus.PENDING,
            time_since_last="no backups found",
        )

    async def _api_get(
        self, client: httpx.AsyncClient, host: str, headers: dict, path: str,
    ) -> Optional[object]:
        url = f"https://{host}:{self.api_port}/api2/json/{path}"
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("data")
        except Exception as e:
            logger.debug("Proxmox API failed for %s: %s", path, e)
            return None
