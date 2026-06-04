"""Data models for backup monitoring."""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BackupStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"
    PENDING = "pending"
    UNKNOWN = "unknown"


class BackupInfo(BaseModel):
    """Information about a single backup job."""

    name: str = Field(description="Backup job name")
    source: str = Field(description="Source server name")
    status: BackupStatus = Field(default=BackupStatus.UNKNOWN)
    status_message: Optional[str] = Field(
        default=None, description="Human-readable status or error"
    )

    # Timing
    last_run: Optional[float] = Field(
        default=None, description="Unix timestamp of last run"
    )
    last_duration: Optional[float] = Field(
        default=None, description="Last backup duration in seconds"
    )
    next_run: Optional[float] = Field(
        default=None, description="Unix timestamp of next scheduled run"
    )
    time_since_last: Optional[str] = Field(
        default=None, description="Human-readable time since last run"
    )
    time_until_next: Optional[str] = Field(
        default=None, description="Human-readable time until next run"
    )

    # Data
    data_transferred: Optional[float] = Field(
        default=None, description="Data transferred in bytes"
    )
    data_transferred_human: Optional[str] = Field(
        default=None, description="Human-readable data size"
    )
    total_size: Optional[float] = Field(
        default=None, description="Total dataset/VM size in bytes"
    )
    total_size_human: Optional[str] = Field(
        default=None, description="Human-readable total size"
    )
    new_data: Optional[float] = Field(
        default=None, description="New data in bytes (incremental)"
    )
    new_data_human: Optional[str] = Field(
        default=None, description="Human-readable new data size"
    )

    # Errors and warnings
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Metadata
    backup_type: str = Field(
        default="", description="Backup type: vzdump, replication, snapshot"
    )
    extra: dict = Field(default_factory=dict)

    model_config = {"json_encoders": {BackupStatus: lambda v: v.value}}


class ServerStatus(BaseModel):
    """Status of a monitored server."""

    name: str
    host: str
    connected: bool = False
    connection_error: Optional[str] = None


class DashboardState(BaseModel):
    """Full dashboard state returned by the API."""

    updated_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp when this data was collected",
    )
    servers: list[ServerStatus] = Field(default_factory=list)
    backups: list[BackupInfo] = Field(default_factory=list)

    @property
    def summary(self) -> dict:
        total = len(self.backups)
        statuses = {}
        for b in self.backups:
            s = b.status.value
            statuses[s] = statuses.get(s, 0) + 1
        return {"total": total, **statuses}
