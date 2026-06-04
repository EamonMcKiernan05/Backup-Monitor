# Backup Monitor

Homelab backup monitoring dashboard with uptime tracking for Proxmox (vzdump), TrueNAS (replication/snapshots), and all homelab hosts.

## Features

- **Uptime Monitor** — ping-based health checks for all Proxmox nodes, TrueNAS servers, and custom hosts
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

**Uptime Monitor** — hosts listed in `health.hosts` are pinged in addition to all Proxmox/TrueNAS targets:

```yaml
health:
  hosts:
    - name: "Windows Desktop"
      ip: "192.168.1.30"
    - name: "MacBook"
      ip: "192.168.2.165"
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/dashboard` | Full dashboard state (includes `health` array) |
| `GET /api/summary` | Quick status summary |
| `GET /api/health` | Health check |

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Proxmox    │     │  TrueNAS    │     │  Extra Hosts │
│  Nodes .7-9 │     │  .12 & .108 │     │  Desktop, etc│
└──────┬──────┘     └──────┬──────┘     └──────┬───────┘
       │                   │                    │
       │ REST API          │ REST API           │ ICMP Ping
       ▼                   ▼                    ▼
┌──────────────────────────────────────────────────────┐
│    Backup Monitor (FastAPI)                           │
│  ┌─────────┐  ┌────────────┐  ┌────────────────┐    │
│  │Proxmox  │  │  TrueNAS   │  │   Uptime       │    │
│  │Collector│  │  Collector │  │   Collector    │    │
│  └─────────┘  └────────────┘  └────────────────┘    │
│         │              │              │              │
│         ▼              ▼              ▼              │
│    ┌─────────────────────────────────────┐          │
│    │        Dashboard API                │          │
│    └───────────────────┬─────────────────┘          │
│                        │                            │
│    ┌───────────────────▼──────────────────┐        │
│    │         Web Dashboard                │        │
│    └──────────────────────────────────────┘        │
└──────────────────────────────────────────────────────┘
```

## Dashboard

The dashboard shows:

**Uptime Monitor** — grid of all monitored hosts with:
- Green dot = reachable, Red dot = unreachable
- Latency in milliseconds
- Auto-refreshes every 60 seconds

**Backup Status** — per-backup cards with:
- Status badge (color-coded)
- Last run time and duration
- Next scheduled run
- Data transferred and new data size
- Errors and warnings (highlighted)

**Proxmox Cluster** — expandable cards showing:
- VM and LXC count per node
- Per-VM/LXC backup status
- Last backup time and errors

## Security

- Credentials are stored in `.env` (not committed)
- SSL verification can be disabled for self-signed certs
- Dashboard serves on configurable port
