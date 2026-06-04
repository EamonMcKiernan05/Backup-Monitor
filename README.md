# Backup Monitor

Homelab backup monitoring dashboard for Proxmox (vzdump) and TrueNAS (replication/snapshots).

## Features

- **Proxmox backup monitoring** — vzdump backup jobs, task logs, schedules
- **TrueNAS backup monitoring** — replication tasks and periodic snapshots
- **Multi-server support** — monitor multiple Proxmox nodes and TrueNAS servers
- **Live dashboard** — auto-refreshing web UI with status indicators
- **REST API** — JSON endpoints for integration with other tools

## Quick Start

```bash
# Clone and install
cd Backup-Monitor
uv sync

# Copy and edit environment
cp .env.example .env

# Start
uv run python main.py
```

Open http://localhost:8501

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in credentials:

| Variable | Description |
|----------|-------------|
| `PROXMOX_PASSWORD` | Proxmox root password |
| `TRUENAS_MAIN_PASSWORD` | Main TrueNAS admin password |
| `TRUENAS_BACKUP_PASSWORD` | Backup TrueNAS admin password |
| `DASHBOARD_PORT` | Dashboard port (default: 8501) |

### config.yaml

Edit `config.yaml` to configure:
- Proxmox nodes and TrueNAS servers
- Refresh interval
- SSL verification settings
- Which backup types to monitor

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/dashboard` | Full dashboard state |
| `GET /api/summary` | Quick status summary |
| `GET /api/health` | Health check |

## Architecture

```
┌─────────────┐     ┌─────────────┐
│  Proxmox    │     │  TrueNAS    │
│  Nodes .7-9 │     │  .12 & .108 │
└──────┬──────┘     └──────┬──────┘
       │                   │
       │ REST API          │ REST API
       ▼                   ▼
┌────────────────────────────────┐
│    Backup Monitor (FastAPI)    │
│  ┌─────────┐  ┌────────────┐  │
│  │Proxmox  │  │  TrueNAS   │  │
│  │Collector│  │  Collector │  │
│  └─────────┘  └────────────┘  │
│         │              │       │
│         ▼              ▼       │
│    ┌─────────────────────┐    │
│    │   Dashboard API     │    │
│    └──────────┬──────────┘    │
│               │               │
│    ┌──────────▼──────────┐    │
│    │   Web Dashboard     │    │
│    └─────────────────────┘    │
└────────────────────────────────┘
```

## Dashboard

The dashboard shows:
- Server connection status (green/red dots)
- Summary counts (total, success, failed, warning, running)
- Per-backup cards with:
  - Status badge (color-coded)
  - Last run time and duration
  - Next scheduled run
  - Data transferred and new data size
  - Errors and warnings (highlighted)

## Security

- Credentials are stored in `.env` (not committed)
- SSL verification can be disabled for self-signed certs
- Dashboard serves on configurable port
