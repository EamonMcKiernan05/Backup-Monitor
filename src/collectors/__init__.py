"""Collectors for backup monitoring."""

from .proxmox import ProxmoxCollector
from .truenas import TrueNasCollector

__all__ = ["ProxmoxCollector", "TrueNasCollector"]
