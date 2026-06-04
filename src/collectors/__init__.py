"""Backup data collectors."""

from .proxmox import ProxmoxCollector
from .truenas import TrueNasCollector

__all__ = ["ProxmoxCollector", "TrueNasCollector"]
